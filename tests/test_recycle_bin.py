"""Native adapter contract tests use fake calls and never empty a real bin."""

from __future__ import annotations

import ctypes
from dataclasses import FrozenInstanceError
from threading import Thread, get_ident
import unittest
from unittest.mock import patch

from clearspace import recycle_bin
from clearspace.recycle_bin import RecycleBinError, RecycleBinSnapshot, WindowsRecycleBin


class FakeShell:
    def __init__(self, *, items=3, size=1234, query_result=0, empty_result=0):
        self.items = items
        self.size = size
        self.query_result = query_result
        self.empty_result = empty_result
        self.queries = []
        self.empties = []

    def SHQueryRecycleBinW(self, root, pointer):
        info = ctypes.cast(pointer, ctypes.POINTER(recycle_bin._SHQUERYRBINFO)).contents
        self.queries.append((root, info.cbSize, get_ident()))
        info.i64NumItems = self.items
        info.i64Size = self.size
        return self.query_result

    def SHEmptyRecycleBinW(self, parent, root, flags):
        self.empties.append((parent, root, flags, get_ident()))
        return self.empty_result


class RecycleBinTests(unittest.TestCase):
    def test_structure_matches_windows_abi(self):
        self.assertEqual(ctypes.sizeof(recycle_bin._SHQUERYRBINFO), 24)
        self.assertEqual(recycle_bin._SHQUERYRBINFO.cbSize.offset, 0)
        self.assertEqual(recycle_bin._SHQUERYRBINFO.i64Size.offset, 8)
        self.assertEqual(recycle_bin._SHQUERYRBINFO.i64NumItems.offset, 16)

    def test_query_uses_all_drives_and_initialized_structure_without_emptying(self):
        api = FakeShell(items=7, size=2**42)
        result = WindowsRecycleBin(api=api).query()
        self.assertEqual(result, RecycleBinSnapshot(items=7, bytes=2**42))
        self.assertEqual(api.queries[0][:2], (None, 24))
        self.assertEqual(api.empties, [])

    def test_empty_uses_native_all_drive_operation_without_dialog_or_sound(self):
        api = FakeShell()
        result = WindowsRecycleBin(api=api).empty()
        self.assertIsNone(result)
        self.assertEqual(api.empties[0][:3], (None, None, 0x7))
        self.assertEqual(api.queries, [])

    def test_snapshot_is_immutable_and_rejects_invalid_totals(self):
        snapshot = RecycleBinSnapshot(items=0, bytes=0)
        with self.assertRaises(FrozenInstanceError):
            snapshot.items = 1
        for values in ((-1, 0), (0, -1), (True, 2), (1, 1.5)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                RecycleBinSnapshot(*values)

    def test_query_native_errors_are_not_interpreted_as_empty(self):
        for code in (0x80070005, ctypes.c_int32(0x80070005).value):
            with self.subTest(code=code), self.assertRaises(RecycleBinError) as caught:
                WindowsRecycleBin(api=FakeShell(query_result=code)).query()
            self.assertEqual(caught.exception.hresult, 0x80070005)
            self.assertIn("read the Recycle Bin", str(caught.exception))
            self.assertIn("Access was denied", str(caught.exception))

    def test_empty_failure_is_reported_and_not_silently_accepted(self):
        with self.assertRaises(RecycleBinError) as caught:
            WindowsRecycleBin(api=FakeShell(empty_result=0x80004005)).empty()
        self.assertEqual(caught.exception.hresult, 0x80004005)
        self.assertIn("empty the Recycle Bin", str(caught.exception))
        self.assertIn("0x80004005", str(caught.exception))

    def test_cancelled_native_operation_has_clear_error(self):
        with self.assertRaisesRegex(RecycleBinError, "operation was cancelled"):
            WindowsRecycleBin(api=FakeShell(empty_result=0x800704C7)).empty()

    def test_malformed_query_totals_are_rejected(self):
        for values in ((-1, 10), (1, -1)):
            with self.subTest(values=values), self.assertRaisesRegex(RecycleBinError, "invalid"):
                WindowsRecycleBin(api=FakeShell(items=values[0], size=values[1])).query()

    def test_native_loading_errors_are_normalized(self):
        with patch.object(recycle_bin, "_shell32", side_effect=OSError("native detail")):
            with self.assertRaisesRegex(RecycleBinError, "could not read"):
                WindowsRecycleBin().query()
            with self.assertRaisesRegex(RecycleBinError, "could not empty"):
                WindowsRecycleBin().empty()

    def test_provider_can_run_from_service_worker_without_main_thread_affinity(self):
        api = FakeShell()
        provider = WindowsRecycleBin(api=api)
        snapshots = []
        failures = []

        def work():
            try:
                snapshots.append(provider.query())
                provider.empty()
            except Exception as error:
                failures.append(error)

        worker = Thread(target=work)
        worker.start()
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(snapshots, [RecycleBinSnapshot(items=3, bytes=1234)])
        self.assertNotEqual(api.queries[0][2], get_ident())
        self.assertEqual(api.queries[0][2], api.empties[0][3])


if __name__ == "__main__":
    unittest.main()
