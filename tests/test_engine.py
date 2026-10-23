"""Integration tests delete only files created in each owned temporary sandbox."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import replace
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from clearspace import windows
from clearspace.engine import Category, Cleaner, DAY


@unittest.skipUnless(os.name == "nt", "Windows handle safety integration tests")
class CleanerTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.TemporaryDirectory(prefix="clearspace-engine-test-")
        self.addCleanup(self.sandbox.cleanup)
        self.directory = Path(self.sandbox.name)
        self.root = self.directory / "cache"
        self.root.mkdir()
        self.category = Category("test", "Test cache", "Owned test fixture", (self.root,), True,
                                 minimum_age_days=7)
        self.cleaner = Cleaner([self.category], process_provider=lambda: set())

    def age_file(self, path: Path, *, creation_days=8, modified_days=8):
        self.assertTrue(path.is_relative_to(self.directory))
        self.assertFalse(path.is_symlink())
        api = windows._kernel32()
        api.SetFileTime.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                   ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME)]
        api.SetFileTime.restype = wintypes.BOOL

        def filetime(days):
            ticks = int((time.time() - days * DAY) * 10_000_000) + windows.WINDOWS_EPOCH_TICKS
            return wintypes.FILETIME(ticks & 0xFFFFFFFF, ticks >> 32)

        value = api.CreateFileW(str(path), 0x100, 7, None, 3,
                               windows.FILE_FLAG_OPEN_REPARSE_POINT, None)
        self.assertNotEqual(value, windows.INVALID_HANDLE_VALUE)
        with windows.Handle(value) as handle:
            created, modified = filetime(creation_days), filetime(modified_days)
            if not api.SetFileTime(handle.value, ctypes.byref(created), None, ctypes.byref(modified)):
                raise ctypes.WinError(ctypes.get_last_error())

    def file(self, name="old.tmp", content=b"old data", *, old=True, root=None):
        path = (root or self.root) / name
        self.assertTrue(path.is_relative_to(self.directory))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        if old:
            self.age_file(path)
        return path

    def junction(self, path, target):
        # CPython's native helper avoids invoking another shell for file work.
        import _winapi
        self.assertTrue(path.is_relative_to(self.directory))
        self.assertTrue(target.is_relative_to(self.directory))
        _winapi.CreateJunction(str(target), str(path))

    def test_windows_structure_layout_and_known_folder(self):
        self.assertEqual(ctypes.sizeof(windows._FILE_DISPOSITION_INFO), 1)
        self.assertEqual(ctypes.sizeof(windows._FILE_BASIC_INFO), 40)
        self.assertEqual(ctypes.sizeof(windows._FILE_STANDARD_INFO), 24)
        self.assertEqual(ctypes.sizeof(windows._FILE_ID_INFO), 24)
        self.assertTrue(windows.local_app_data().is_absolute())
        self.assertTrue(windows.roaming_app_data().is_absolute())
        self.assertTrue(windows.local_low_app_data().is_absolute())
        self.assertTrue(windows.common_app_data().is_absolute())
        self.assertTrue(windows.windows_directory().is_absolute())
        self.assertIn(Path(sys.executable).name.casefold(), windows.running_processes())

    def test_known_folder_helpers_ignore_environment_spoofing(self):
        helpers = (windows.local_app_data, windows.roaming_app_data, windows.local_low_app_data,
                   windows.common_app_data, windows.windows_directory)
        expected = tuple(helper() for helper in helpers)
        fake = str(self.directory / "wrong")
        with patch.dict(os.environ, {"LOCALAPPDATA": fake, "APPDATA": fake,
                                     "PROGRAMDATA": fake, "WINDIR": fake}):
            self.assertEqual(tuple(helper() for helper in helpers), expected)

    def test_analysis_is_read_only_and_only_cleanup_removes_aged_files(self):
        old = self.file("nested/old.tmp", b"12345")
        fresh = self.file("recent.tmp", b"keep", old=False)
        plan = self.cleaner.scan(["test"])
        self.assertEqual(plan.total_files, 1)
        self.assertEqual(plan.total_bytes, 5)
        self.assertEqual(old.read_bytes(), b"12345")
        self.assertEqual(fresh.read_bytes(), b"keep")
        result = self.cleaner.clean(plan, ["test"])
        self.assertEqual((result.deleted_files, result.freed_bytes), (1, 5))
        self.assertFalse(old.exists())
        self.assertTrue(old.parent.is_dir(), "Empty directories are deliberately preserved")
        self.assertEqual(fresh.read_bytes(), b"keep")

    def test_default_rule_includes_new_files_without_an_age_limit(self):
        fresh = self.file("recent.tmp", b"new temporary data", old=False)
        category = Category("test", "Test cache", "Owned test fixture", (self.root,), True)
        self.assertEqual(category.minimum_age_days, 0)
        cleaner = Cleaner([category], process_provider=lambda: set())
        plan = cleaner.scan(["test"])
        self.assertEqual(plan.total_files, 1)
        self.assertEqual(fresh.read_bytes(), b"new temporary data")
        self.assertEqual(cleaner.clean(plan, ["test"]).deleted_files, 1)
        self.assertFalse(fresh.exists())

    def test_file_patterns_are_case_insensitive_and_exclusions_are_preserved(self):
        selected = self.file("nested/THUMBCACHE_256.DB", b"cache", old=False)
        unselected = self.file("saved.db", b"personal", old=False)
        excluded = self.file("thumbcache_keep.db", b"excluded", old=False)
        category = Category("test", "Thumbnail cache", "Owned fixture", (self.root,), True,
                            file_patterns=("thumbcache_*.db",),
                            exclude_patterns=("thumbcache_keep.db",), group="Windows")
        cleaner = Cleaner([category], process_provider=lambda: set())
        plan = cleaner.scan(["test"])
        self.assertEqual([entry.path for entry in plan.entries], [selected])
        self.assertEqual(category.to_dict()["group"], "Windows")
        self.assertEqual(category.to_dict()["file_patterns"], ["thumbcache_*.db"])
        new_match = self.file("thumbcache_new.db", b"after analysis", old=False)
        result = cleaner.clean(plan, ["test"])
        self.assertEqual(result.deleted_files, 1)
        self.assertFalse(selected.exists())
        self.assertTrue(unselected.exists())
        self.assertTrue(excluded.exists())
        self.assertTrue(new_match.exists())

    def test_open_directory_writer_does_not_hide_the_entire_category(self):
        path = self.file()
        api = windows._kernel32()
        value = api.CreateFileW(str(self.root), 0x2, 7, None, 3,
                               windows.FILE_FLAG_BACKUP_SEMANTICS | windows.FILE_FLAG_OPEN_REPARSE_POINT, None)
        self.assertNotEqual(value, windows.INVALID_HANDLE_VALUE)
        with windows.Handle(value):
            plan = self.cleaner.scan(["test"])
            self.assertEqual(plan.total_files, 1)
            self.assertEqual(self.cleaner.clean(plan, ["test"]).deleted_files, 1)
        self.assertFalse(path.exists())

    def test_nonrecursive_rule_only_considers_files_directly_in_its_root(self):
        selected = self.file("thumbcache_256.db", old=False)
        nested = self.file("subfolder/thumbcache_256.db", old=False)
        category = Category("test", "Thumbnail cache", "Owned fixture", (self.root,), True,
                            file_patterns=("thumbcache_*.db",), recursive=False)
        cleaner = Cleaner([category], process_provider=lambda: set())
        plan = cleaner.scan(["test"])
        self.assertFalse(plan.truncated)
        self.assertEqual([entry.path for entry in plan.entries], [selected])
        self.assertEqual(cleaner.clean(plan, ["test"]).deleted_files, 1)
        self.assertTrue(nested.exists())

    def test_fresh_creation_with_old_modified_timestamp_is_kept(self):
        path = self.file(old=False)
        self.age_file(path, creation_days=0, modified_days=60)
        plan = self.cleaner.scan(["test"])
        self.assertEqual(plan.total_files, 0)
        self.cleaner.clean(plan, ["test"])
        self.assertTrue(path.exists())

    def test_old_creation_with_recent_modified_timestamp_is_kept(self):
        path = self.file(old=False)
        self.age_file(path, creation_days=60, modified_days=0)
        self.assertEqual(self.cleaner.scan(["test"]).total_files, 0)
        self.assertTrue(path.exists())

    def test_cleanup_only_selected_categories_and_snapshotted_files(self):
        other = self.directory / "other"
        other.mkdir()
        keep_category = Category("other", "Other", "Owned fixture", (other,))
        cleaner = Cleaner([self.category, keep_category], process_provider=lambda: set())
        selected = self.file(content=b"123")
        unselected = self.file(root=other)
        plan = cleaner.scan(["test", "other"])
        added_after_scan = self.file("not-in-plan.tmp")
        result = cleaner.clean(plan, ["test"])
        self.assertEqual(result.deleted_files, 1)
        self.assertFalse(selected.exists())
        self.assertTrue(unselected.exists())
        self.assertTrue(added_after_scan.exists())

    def test_changed_file_is_kept_even_if_modified_time_is_restored(self):
        path = self.file(content=b"before")
        plan = self.cleaner.scan(["test"])
        snapshot = plan.entries[0]._snapshot
        time.sleep(0.01)
        path.write_bytes(b"after!")
        # Restore scan-time creation/write times exactly; ChangeTime still changes.
        api = windows._kernel32()
        value = api.CreateFileW(str(path), 0x100, 7, None, 3, windows.FILE_FLAG_OPEN_REPARSE_POINT, None)
        with windows.Handle(value) as handle:
            created = wintypes.FILETIME(snapshot.creation_ticks & 0xFFFFFFFF, snapshot.creation_ticks >> 32)
            modified = wintypes.FILETIME(snapshot.modified_ticks & 0xFFFFFFFF, snapshot.modified_ticks >> 32)
            self.assertTrue(api.SetFileTime(handle.value, ctypes.byref(created), None, ctypes.byref(modified)))
        result = self.cleaner.clean(plan, ["test"])
        self.assertEqual((result.deleted_files, result.skipped_files), (0, 1))
        self.assertEqual(path.read_bytes(), b"after!")

    def test_replaced_file_with_same_size_and_old_timestamps_is_kept(self):
        path = self.file(content=b"first")
        plan = self.cleaner.scan(["test"])
        path.rename(self.root / "original.tmp")
        replacement = self.file(content=b"other")
        result = self.cleaner.clean(plan, ["test"])
        self.assertEqual((result.deleted_files, result.skipped_files), (0, 1))
        self.assertEqual(replacement.read_bytes(), b"other")

    def test_replaced_root_is_kept(self):
        self.file()
        plan = self.cleaner.scan(["test"])
        self.root.rename(self.directory / "original")
        self.root.mkdir()
        replacement = self.file()
        result = self.cleaner.clean(plan, ["test"])
        self.assertEqual((result.deleted_files, result.skipped_files), (0, 1))
        self.assertTrue(replacement.exists())
        self.assertTrue((self.directory / "original/old.tmp").exists())

    def test_replaced_parent_directory_is_kept(self):
        self.file("nested/old.tmp")
        plan = self.cleaner.scan(["test"])
        (self.root / "nested").rename(self.root / "original")
        replacement = self.file("nested/old.tmp")
        result = self.cleaner.clean(plan, ["test"])
        self.assertEqual((result.deleted_files, result.skipped_files), (0, 1))
        self.assertTrue(replacement.exists())

    def test_links_are_excluded_from_analysis(self):
        outside = self.directory / "outside"
        outside.mkdir()
        target = self.file(root=outside)
        self.junction(self.root / "link", outside)
        hardlink = self.root / "hardlink.tmp"
        os.link(target, hardlink)
        plan = self.cleaner.scan(["test"])
        self.assertEqual(plan.total_files, 0)
        self.assertEqual(self.cleaner.clean(plan, ["test"]).deleted_files, 0)
        self.assertEqual(target.read_bytes(), b"old data")

    def test_root_junction_is_excluded_from_analysis(self):
        outside = self.directory / "outside"
        outside.mkdir()
        target = self.file(root=outside)
        self.root.rmdir()
        self.junction(self.root, outside)
        plan = self.cleaner.scan(["test"])
        self.assertEqual(plan.total_files, 0)
        self.assertTrue(plan.category_stats[0].reason)
        self.assertTrue(target.exists())

    def test_parent_replaced_with_junction_after_scan_keeps_external_file(self):
        self.file("nested/old.tmp")
        plan = self.cleaner.scan(["test"])
        outside = self.directory / "outside"
        outside.mkdir()
        target = self.file(root=outside)
        (self.root / "nested").rename(self.root / "original")
        self.junction(self.root / "nested", outside)
        result = self.cleaner.clean(plan, ["test"])
        self.assertEqual((result.deleted_files, result.skipped_files), (0, 1))
        self.assertTrue(target.exists())
        self.assertTrue((self.root / "original/old.tmp").exists())

    def test_pinned_ancestors_block_rename_during_final_file_open(self):
        path = self.file("nested/old.tmp")
        plan = self.cleaner.scan(["test"])
        original_open = windows.open_handle
        rename_attempts = []

        def racing_open(candidate, *, directory=False, delete=False):
            if delete:
                with self.assertRaises(OSError):
                    path.parent.rename(self.root / "moved")
                rename_attempts.append(True)
            return original_open(candidate, directory=directory, delete=delete)

        with patch("clearspace.windows.open_handle", side_effect=racing_open):
            result = self.cleaner.clean(plan, ["test"])
        self.assertEqual(rename_attempts, [True])
        self.assertEqual(result.deleted_files, 1)

    def test_existing_reader_allowing_delete_still_makes_cleanup_skip(self):
        path = self.file()
        plan = self.cleaner.scan(["test"])
        api = windows._kernel32()
        value = api.CreateFileW(str(path), windows.FILE_READ_DATA, 7, None, 3, 0, None)
        self.assertNotEqual(value, windows.INVALID_HANDLE_VALUE)
        with windows.Handle(value):
            result = self.cleaner.clean(plan, ["test"])
        self.assertEqual((result.deleted_files, result.skipped_files), (0, 1))
        self.assertTrue(path.exists())

    def test_analysis_probe_skips_files_already_in_use_without_deleting(self):
        path = self.file()
        api = windows._kernel32()
        value = api.CreateFileW(str(path), windows.FILE_READ_DATA, 7, None, 3, 0, None)
        self.assertNotEqual(value, windows.INVALID_HANDLE_VALUE)
        with windows.Handle(value):
            plan = self.cleaner.scan(["test"])
        self.assertEqual((plan.total_files, plan.skipped_files), (0, 1))
        self.assertEqual(path.read_bytes(), b"old data")

    def test_readonly_files_are_not_reported_as_removable(self):
        path = self.file()
        path.chmod(0o400)
        self.addCleanup(path.chmod, 0o600)
        plan = self.cleaner.scan(["test"])
        self.assertEqual(plan.total_files, 0)
        self.assertTrue(path.exists())

    def test_plan_authority_is_not_replayable_or_transferable(self):
        self.file()
        plan = self.cleaner.scan(["test"])
        other = Cleaner([self.category], process_provider=lambda: set())
        with self.assertRaises(ValueError):
            other.clean(plan, ["test"])
        with self.assertRaises(ValueError):
            self.cleaner.clean(replace(plan), ["test"])
        self.cleaner.clean(plan, ["test"])
        with self.assertRaises(ValueError):
            self.cleaner.clean(plan, ["test"])

    def test_new_analysis_invalidates_previous_plan_and_expiry_requires_rescan(self):
        path = self.file()
        previous = self.cleaner.scan(["test"])
        current = self.cleaner.scan(["test"])
        with self.assertRaises(ValueError):
            self.cleaner.clean(previous, ["test"])
        with patch("clearspace.engine.time.time", return_value=current.expires_at + 1):
            with self.assertRaises(ValueError):
                self.cleaner.clean(current, ["test"])
        self.assertTrue(path.exists())

    def test_cancelled_and_truncated_analyses_cannot_delete_files(self):
        path = self.file()
        self.file("second.tmp")
        cancel = threading.Event()
        cancel.set()
        cancelled = self.cleaner.scan(["test"], cancel=cancel)
        self.assertTrue(cancelled.cancelled)
        with self.assertRaises(ValueError):
            self.cleaner.clean(cancelled, ["test"])
        bounded = Cleaner([self.category], max_files=1, process_provider=lambda: set())
        truncated = bounded.scan(["test"])
        self.assertTrue(truncated.truncated)
        with self.assertRaises(ValueError):
            bounded.clean(truncated, ["test"])
        self.assertTrue(path.exists())

    def test_cleanup_cancel_stops_after_current_file_and_consumes_plan(self):
        self.file("first.tmp")
        self.file("second.tmp")
        plan = self.cleaner.scan(["test"])
        cancel = threading.Event()
        original_delete = windows.Handle.mark_for_deletion

        def delete_then_cancel(handle):
            original_delete(handle)
            cancel.set()

        with patch.object(windows.Handle, "mark_for_deletion", delete_then_cancel):
            result = self.cleaner.clean(plan, ["test"], cancel=cancel)
        self.assertEqual((result.deleted_files, result.skipped_files), (1, 1))
        self.assertTrue(result.cancelled)
        self.assertEqual(len(list(self.root.iterdir())), 1)
        with self.assertRaises(ValueError):
            self.cleaner.clean(plan, ["test"])

    def test_running_app_and_failed_process_enumeration_fail_closed(self):
        path = self.file()
        category = replace(self.category, processes=("chrome.exe",))
        running = Cleaner([category], process_provider=lambda: {"CHROME.EXE"})
        plan = running.scan(["test"])
        self.assertEqual(plan.total_files, 0)
        self.assertIn("Close", plan.category_stats[0].reason)

        def unknown():
            raise OSError("Process enumeration unavailable")

        unavailable = Cleaner([category], process_provider=unknown)
        plan = unavailable.scan(["test"])
        self.assertEqual(plan.total_files, 0)
        self.assertIn("Could not check", plan.category_stats[0].reason)
        self.assertTrue(path.exists())

    def test_app_opened_after_analysis_keeps_its_cache(self):
        path = self.file()
        processes = set()
        category = replace(self.category, processes=("chrome.exe",))
        cleaner = Cleaner([category], process_provider=lambda: processes)
        plan = cleaner.scan(["test"])
        processes.add("chrome.exe")
        result = cleaner.clean(plan, ["test"])
        self.assertEqual((result.deleted_files, result.skipped_files), (0, 1))
        self.assertTrue(path.exists())

    def test_progress_reports_counts_and_callback_failure_does_not_lose_result(self):
        self.file()
        updates = []
        plan = self.cleaner.scan(["test"], progress=updates.append)
        self.assertEqual(updates[-1]["found_files"], 1)
        self.assertEqual(updates[-1]["phase"], "scanning")

        def broken(_):
            raise RuntimeError("Renderer disconnected")

        result = self.cleaner.clean(plan, ["test"], progress=broken)
        self.assertEqual(result.deleted_files, 1)

    def test_invalid_category_and_ambiguous_paths_are_rejected(self):
        with self.assertRaises(ValueError):
            self.cleaner.scan(["unknown"])
        with self.assertRaises(ValueError):
            self.cleaner.scan("test")
        with self.assertRaises(ValueError):
            self.cleaner.scan([])
        for path in [Path("relative"), self.root / ".." / "outside", Path(r"\\server\share\cache")]:
            with self.assertRaises(windows.UnsafePathError):
                windows.supported_path(path)
        for category in [replace(self.category, minimum_age_days=-1),
                         replace(self.category, file_patterns=["*.tmp"]),
                         replace(self.category, file_patterns=("../*.tmp",))]:
            with self.assertRaises(ValueError):
                Cleaner([category])


if __name__ == "__main__":
    unittest.main()
