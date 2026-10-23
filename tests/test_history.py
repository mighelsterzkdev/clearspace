"""History cleanup tests use in-memory registries or a new owned HKCU fixture.

No test reads or clears the user's actual Run, Search, RecentDocs, or app list.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
import os
from threading import Event
import unittest
from unittest.mock import patch
import uuid

from clearspace import history
from clearspace.history import HistoryChangedError, HistoryError, HistorySpec, WindowsHistory


class MemoryRegistry(history._WindowsRegistry):
    def __init__(self, category_id="run_history"):
        self.spec = history._SPECS[category_id]
        self.registry = self
        self.keys = {
            self.spec.path: {
                "revision": 10,
                "children": (),
                "values": {
                    "": ("preserved default", 1),
                    "a": ("example command", 1),
                    "b": ("another command", 1),
                    "MRUList": ("ba", 1),
                    "KeepSetting": (42, 4),
                },
            },
        }
        self.deletions = []
        self.after_delete = None
        self.before_read = None
        self.failure = None
        self.reads = 0

    @contextmanager
    def _open(self, path, *, writable=False):
        if path not in self.keys:
            raise FileNotFoundError(path)
        yield path

    def _read_open_key(self, key, path):
        self.reads += 1
        if self.before_read:
            self.before_read(self, path)
        data = self.keys[path]
        return history._Key(
            path, data["revision"],
            tuple(sorted((history._Value(name, kind, history._freeze(value))
                          for name, (value, kind) in data["values"].items()),
                         key=lambda item: item.name.casefold())),
            data["children"],
        )

    def DeleteValue(self, key, name):
        if self.failure:
            raise self.failure
        self.deletions.append((key, name))
        del self.keys[key]["values"][name]
        self.keys[key]["revision"] += 1
        if self.after_delete:
            self.after_delete(self, key, name)


class HistoryTests(unittest.TestCase):
    def provider(self, category_id="run_history"):
        backend = MemoryRegistry(category_id)
        return WindowsHistory(category_id, backend=backend), backend

    def test_only_fixed_history_ids_are_publicly_accepted(self):
        self.assertEqual(set(history.history_providers()), {
            "run_history", "explorer_search_history", "recent_document_history"})
        for invalid in ("", "userassist", "cloudstore", "C:\\Windows", "..\\Other", None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                WindowsHistory(invalid)

    def test_search_settings_uri_is_documented_fixed_destination(self):
        self.assertEqual(history.SEARCH_HISTORY_SETTINGS_URI, "ms-settings:search-permissions")

    def test_query_is_read_only_and_counts_entries_without_order_or_settings(self):
        provider, backend = self.provider()
        snapshot = provider.query()
        self.assertEqual(snapshot.items, 2)
        self.assertEqual(snapshot.bytes, 0)
        self.assertTrue(snapshot.pending)
        self.assertEqual(backend.deletions, [])
        self.assertIn("Referenced files and pinned items are kept", snapshot.detail)

    def test_snapshot_and_nested_data_are_immutable(self):
        provider, _ = self.provider()
        snapshot = provider.query()
        with self.assertRaises(FrozenInstanceError):
            snapshot.items = 99
        with self.assertRaises(FrozenInstanceError):
            snapshot.keys[0].values[0].data = "changed"

    def test_clean_only_known_history_values_preserving_key_default_and_settings(self):
        provider, backend = self.provider()
        snapshot = provider.query()
        result = provider.clean(snapshot)
        self.assertTrue(result.completed)
        self.assertEqual(result.removed_items, 2)
        self.assertEqual(result.freed_bytes, 0)
        self.assertEqual(backend.keys[backend.spec.path]["values"], {
            "": ("preserved default", 1), "KeepSetting": (42, 4)})
        self.assertEqual([name for _, name in backend.deletions], ["a", "b", "MRUList"])

    def test_forged_snapshot_and_other_provider_snapshot_are_rejected(self):
        provider, backend = self.provider()
        snapshot = provider.query()
        for invalid in (replace(snapshot), self.provider()[0].query()):
            with self.subTest(invalid=invalid), self.assertRaises(HistoryError):
                provider.clean(invalid)
        self.assertEqual(backend.deletions, [])
        self.assertTrue(provider.clean(snapshot).completed)

    def test_missing_or_wrong_type_snapshot_has_controlled_error(self):
        provider, backend = self.provider()
        for invalid in (None, {}, "snapshot", 123):
            with self.subTest(invalid=invalid), self.assertRaises(HistoryError):
                provider.clean(invalid)
        self.assertEqual(backend.deletions, [])

    def test_snapshot_is_consumed_once(self):
        provider, _ = self.provider()
        snapshot = provider.query()
        provider.clean(snapshot)
        with self.assertRaises(HistoryError):
            provider.clean(snapshot)

    def test_new_query_invalidates_older_snapshot(self):
        provider, backend = self.provider()
        old = provider.query()
        latest = provider.query()
        with self.assertRaises(HistoryError):
            provider.clean(old)
        self.assertEqual(backend.deletions, [])
        self.assertTrue(provider.clean(latest).completed)

    def test_changed_same_size_data_since_analysis_is_kept(self):
        provider, backend = self.provider()
        snapshot = provider.query()
        backend.keys[backend.spec.path]["values"]["a"] = ("different value", 1)
        with self.assertRaises(HistoryChangedError):
            provider.clean(snapshot)
        self.assertEqual(backend.deletions, [])

    def test_changed_revision_with_same_contents_is_kept(self):
        provider, backend = self.provider()
        snapshot = provider.query()
        backend.keys[backend.spec.path]["revision"] += 1
        with self.assertRaises(HistoryChangedError):
            provider.clean(snapshot)
        self.assertEqual(backend.deletions, [])

    def test_removed_key_is_reported_as_changed_and_not_created(self):
        provider, backend = self.provider()
        snapshot = provider.query()
        backend.keys.clear()
        with self.assertRaises(HistoryChangedError):
            provider.clean(snapshot)
        self.assertEqual(backend.keys, {})

    def test_cancellation_before_cleanup_consumes_snapshot_without_deleting(self):
        provider, backend = self.provider()
        snapshot = provider.query()
        cancel = Event()
        cancel.set()
        result = provider.clean(snapshot, cancel)
        self.assertFalse(result.completed)
        self.assertEqual(result.removed_items, 0)
        self.assertEqual(backend.deletions, [])
        with self.assertRaises(HistoryError):
            provider.clean(snapshot)

    def test_cancellation_between_entries_keeps_rest_and_mru_index(self):
        provider, backend = self.provider()
        snapshot = provider.query()
        cancel = Event()
        backend.after_delete = lambda *args: cancel.set()
        result = provider.clean(snapshot, cancel)
        self.assertFalse(result.completed)
        self.assertEqual(result.removed_items, 1)
        self.assertIn("b", backend.keys[backend.spec.path]["values"])
        self.assertIn("MRUList", backend.keys[backend.spec.path]["values"])

    def test_concurrent_new_history_keeps_new_entry_and_order_index(self):
        provider, backend = self.provider()
        snapshot = provider.query()

        def change(registry, key, name):
            registry.keys[key]["values"]["c"] = ("new command", 1)
            registry.keys[key]["values"]["MRUList"] = ("cba", 1)
            registry.keys[key]["revision"] += 1

        backend.after_delete = change
        result = provider.clean(snapshot)
        self.assertFalse(result.completed)
        self.assertEqual(result.removed_items, 1)
        self.assertEqual(backend.keys[backend.spec.path]["values"]["c"], ("new command", 1))
        self.assertEqual(backend.keys[backend.spec.path]["values"]["MRUList"], ("cba", 1))

    def test_failure_is_partial_and_does_not_claim_history_cleared(self):
        provider, backend = self.provider()
        snapshot = provider.query()
        backend.failure = PermissionError("fixture access denied")
        result = provider.clean(snapshot)
        self.assertFalse(result.completed)
        self.assertEqual(result.removed_items, 0)
        self.assertIn("remaining entries were kept", result.detail)

    def test_empty_or_absent_history_is_not_pending(self):
        provider, backend = self.provider()
        backend.keys.clear()
        snapshot = provider.query()
        self.assertFalse(snapshot.pending)
        self.assertEqual(snapshot.items, 0)
        self.assertTrue(provider.clean(snapshot).completed)
        self.assertEqual(backend.deletions, [])

    def test_recentdocs_clears_only_numeric_binary_entries_in_known_children(self):
        provider, backend = self.provider("recent_document_history")
        root = backend.spec.path
        backend.keys[root] = {"revision": 1, "children": (".png",), "values": {
            "0": (b"recent document metadata", 3), "MRUListEx": (b"index", 3),
            "DefaultSetting": (b"keep", 3), "wrong_type": ("keep", 1)}}
        backend.keys[root + r"\.png"] = {"revision": 2, "children": (), "values": {
            "0": (b"another document metadata", 3), "MRUListEx": (b"index", 3),
            "1": ("unexpected string", 1)}}
        snapshot = provider.query()
        self.assertEqual(snapshot.items, 2)
        result = provider.clean(snapshot)
        self.assertTrue(result.completed)
        self.assertEqual(result.removed_items, 2)
        self.assertEqual(backend.keys[root + r"\.png"]["values"], {"1": ("unexpected string", 1)})
        self.assertIn("DefaultSetting", backend.keys[root]["values"])

    def test_all_child_keys_are_checked_before_any_parent_history_is_deleted(self):
        provider, backend = self.provider("recent_document_history")
        root = backend.spec.path
        backend.keys[root] = {"revision": 1, "children": (".png",),
                              "values": {"0": (b"root history", 3)}}
        backend.keys[root + r"\.png"] = {"revision": 2, "children": (),
                                          "values": {"0": (b"child history", 3)}}
        snapshot = provider.query()
        backend.keys[root + r"\.png"]["revision"] += 1
        with self.assertRaises(HistoryChangedError):
            provider.clean(snapshot)
        self.assertEqual(backend.deletions, [])

    def test_unknown_or_unicode_numeric_values_are_preserved(self):
        provider, backend = self.provider("explorer_search_history")
        backend.keys[backend.spec.path]["values"] = {
            "0": (b"search term", 3), "MRUListEx": (b"index", 3),
            "١": (b"not a supported value", 3), "1": ("not binary", 1)}
        result = provider.clean(provider.query())
        self.assertTrue(result.completed)
        self.assertEqual(result.removed_items, 1)
        self.assertEqual(set(backend.keys[backend.spec.path]["values"]), {"١", "1"})

    def test_read_errors_are_normalized_and_invalidate_previous_snapshot(self):
        provider, backend = self.provider()
        previous = provider.query()
        with patch.object(backend, "read", side_effect=PermissionError("private registry detail")):
            with self.assertRaisesRegex(HistoryError, "could not read"):
                provider.query()
        with self.assertRaises(HistoryError):
            provider.clean(previous)

    def test_freeze_does_not_retain_mutable_registry_data(self):
        values = ["one", "two"]
        result = history._freeze(values)
        values.append("three")
        self.assertEqual(result, ("one", "two"))
        with self.assertRaises(HistoryError):
            history._freeze(bytearray(b"mutable"))
        with self.assertRaises(HistoryError):
            history._freeze((["nested mutable"],))
        with self.assertRaises(HistoryError):
            history._freeze(b"x" * (history._MAX_VALUE_BYTES + 1))


@unittest.skipUnless(os.name == "nt", "Windows registry fixture")
class NativeHistoryFixtureTests(unittest.TestCase):
    """Create and clear only a new unique test key; actual histories are untouched."""

    def setUp(self):
        import winreg
        self.reg = winreg
        self.path = r"Software\Clearspace-owned-test-" + uuid.uuid4().hex
        self.spec = HistorySpec("run_history", "Owned fixture", self.path)
        self.backend = history._WindowsRegistry()
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self.path) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "owned default")
            winreg.SetValueEx(key, "a", 0, winreg.REG_SZ, "owned history")
            winreg.SetValueEx(key, "MRUList", 0, winreg.REG_SZ, "a")
            winreg.SetValueEx(key, "KeepSetting", 0, winreg.REG_DWORD, 7)

    def tearDown(self):
        # Exact generated HKCU fixture, no recursive deletion or user key input.
        self.assertTrue(self.path.startswith("Software\\Clearspace-owned-test-"))
        self.reg.DeleteKeyEx(self.reg.HKEY_CURRENT_USER, self.path,
                             self.reg.KEY_WOW64_64KEY, 0)

    def snapshot(self):
        keys = self.backend.read(self.spec)
        return history.HistorySnapshot(self.spec.id, keys, items=1)

    def test_native_read_is_read_only_and_cleanup_preserves_fixture_settings(self):
        snapshot = self.snapshot()
        self.assertEqual(len(snapshot.keys[0].values), 4)
        result = self.backend.clear(self.spec, snapshot)
        self.assertTrue(result.completed)
        self.assertEqual(result.removed_items, 1)
        values = self.backend.read(self.spec)[0].values
        self.assertEqual({item.name for item in values}, {"", "KeepSetting"})

    def test_native_changed_fixture_does_not_delete_new_history(self):
        snapshot = self.snapshot()
        with self.reg.OpenKey(self.reg.HKEY_CURRENT_USER, self.path, 0, self.reg.KEY_SET_VALUE) as key:
            self.reg.SetValueEx(key, "b", 0, self.reg.REG_SZ, "new owned command")
        with self.assertRaises(HistoryChangedError):
            self.backend.clear(self.spec, snapshot)
        self.assertEqual(len(self.backend.read(self.spec)[0].values), 5)

    def test_native_read_limit_fails_without_modifying_fixture(self):
        with patch.object(history, "_MAX_VALUES", 1):
            with self.assertRaisesRegex(HistoryError, "analysis limit"):
                self.backend.read(self.spec)
        self.assertEqual(len(self.backend.read(self.spec)[0].values), 4)


if __name__ == "__main__":
    unittest.main()
