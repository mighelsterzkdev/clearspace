"""Bin workflow tests inject a fake provider; never empty the real Recycle Bin."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest

from clearspace.engine import Category, Cleaner
from clearspace.recycle_bin import RecycleBinSnapshot
from clearspace.service import CleanerService, ServiceError


BIN = Category("recycle_bin", "Recycle Bin", "Deleted items", (), native_action="empty_recycle_bin")


class FakeBin:
    def __init__(self, items=3, size=600):
        self.snapshot = RecycleBinSnapshot(items, size)
        self.empty_calls = 0
        self.query_calls = 0
        self.fail_query_at = None
        self.fail_empty = False
        self.remaining = RecycleBinSnapshot(0, 0)

    def query(self):
        self.query_calls += 1
        if self.query_calls == self.fail_query_at:
            raise OSError("Fake query failure")
        return self.snapshot

    def empty(self):
        self.empty_calls += 1
        self.snapshot = self.remaining
        if self.fail_empty:
            raise OSError("Fake partial empty failure")


class BinWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.bin = FakeBin()
        self.cleaner = Cleaner([BIN], recycle_bin=self.bin)

    def test_analysis_only_queries_and_bin_only_cleanup_consumes_plan(self):
        plan = self.cleaner.scan([BIN.id])
        self.assertEqual(plan.entries, ())
        self.assertEqual((plan.total_files, plan.total_bytes), (3, 600))
        self.assertEqual(self.bin.empty_calls, 0)
        with self.assertRaises(ValueError):
            self.cleaner.clean(replace(plan), [BIN.id])
        result = self.cleaner.clean(plan, [BIN.id])
        self.assertEqual((result.deleted_files, result.freed_bytes), (3, 600))
        self.assertEqual(self.bin.empty_calls, 1)
        with self.assertRaises(ValueError):
            self.cleaner.clean(plan, [BIN.id])

    def test_changed_count_or_size_keeps_bin_and_reports_reanalysis(self):
        for changed in (RecycleBinSnapshot(4, 600), RecycleBinSnapshot(3, 601)):
            with self.subTest(changed=changed):
                self.bin.snapshot = RecycleBinSnapshot(3, 600)
                plan = self.cleaner.scan([BIN.id])
                self.bin.snapshot = changed
                result = self.cleaner.clean(plan, [BIN.id])
                self.assertEqual((self.bin.empty_calls, result.deleted_files, result.skipped_files), (0, 0, 3))
                self.assertIn("changed since analysis", result.issues[0]["reason"])

    def test_cancellation_before_native_call_preserves_bin(self):
        plan = self.cleaner.scan([BIN.id])
        cancel = threading.Event()
        cancel.set()
        result = self.cleaner.clean(plan, [BIN.id], cancel=cancel)
        self.assertTrue(result.cancelled)
        self.assertEqual((self.bin.empty_calls, result.skipped_files), (0, 3))

    def test_cancel_during_preflight_query_prevents_native_call(self):
        plan = self.cleaner.scan([BIN.id])
        cancel = threading.Event()
        query = self.bin.query

        def cancelling_query():
            cancel.set()
            return query()

        self.bin.query = cancelling_query
        result = self.cleaner.clean(plan, [BIN.id], cancel=cancel)
        self.assertTrue(result.cancelled)
        self.assertEqual(self.bin.empty_calls, 0)

    def test_empty_bin_never_calls_native_empty(self):
        self.bin.snapshot = RecycleBinSnapshot(0, 0)
        plan = self.cleaner.scan([BIN.id])
        result = self.cleaner.clean(plan, [BIN.id])
        self.assertEqual((self.bin.empty_calls, result.deleted_files), (0, 0))

    def test_query_failure_cannot_authorize_bin_cleanup(self):
        self.bin.fail_query_at = 1
        plan = self.cleaner.scan([BIN.id])
        self.assertIsNone(plan.recycle_bin)
        self.assertIn("could not read", plan.category_stats[0].reason)
        result = self.cleaner.clean(plan, [BIN.id])
        self.assertEqual((self.bin.empty_calls, result.deleted_files), (0, 0))

    def test_partial_native_failure_is_queried_and_reported(self):
        self.bin.fail_empty = True
        self.bin.remaining = RecycleBinSnapshot(1, 100)
        plan = self.cleaner.scan([BIN.id])
        result = self.cleaner.clean(plan, [BIN.id])
        self.assertEqual((result.deleted_files, result.freed_bytes, result.failed_files), (2, 500, 1))
        self.assertEqual(self.bin.query_calls, 3)
        self.assertTrue(result.issues)

    def test_unverified_native_result_never_claims_success(self):
        self.bin.fail_query_at = 3
        plan = self.cleaner.scan([BIN.id])
        result = self.cleaner.clean(plan, [BIN.id])
        self.assertEqual((result.deleted_files, result.freed_bytes, result.failed_files), (0, 0, 3))
        self.assertIn("could not verify", result.issues[0]["reason"])

    def test_invalid_native_rules_are_rejected(self):
        for category in (replace(BIN, native_action="delete_everything"),
                         replace(BIN, roots=(Path("C:/Windows"),)),
                         replace(BIN, id="other_bin")):
            with self.assertRaises(ValueError):
                Cleaner([category], recycle_bin=self.bin)

    def test_bin_only_service_requires_matching_selection_and_scan(self):
        with TemporaryDirectory(prefix="clearspace-bin-service-test-") as directory:
            service = CleanerService(self.cleaner, directory)
            try:
                service.set_selection([BIN.id])
                with self.assertRaises(ServiceError):
                    service.clean("forged", [BIN.id])
                service.scan()
                state = self.wait(service, "ready")
                self.assertEqual(self.bin.empty_calls, 0)
                self.assertEqual(state["categories"][0]["native_action"], "empty_recycle_bin")
                with self.assertRaises(ServiceError):
                    service.get_files(state["scan_id"], BIN.id)
                with self.assertRaises(ServiceError):
                    service.clean(state["scan_id"], [])
                service.clean(state["scan_id"], [BIN.id])
                result = self.wait(service, "completed")
                self.assertEqual(result["result"]["removed"], 3)
                with self.assertRaises(ServiceError):
                    service.clean(state["scan_id"], [BIN.id])
            finally:
                service.shutdown()

    def test_selection_excluding_bin_cannot_empty_it(self):
        with TemporaryDirectory(prefix="clearspace-bin-mixed-test-") as directory:
            folder = Path(directory)
            owned = folder / "cache.tmp"
            owned.write_bytes(b"owned temporary fixture")
            cache = Category("cache", "Cache", "Owned", (folder,))
            cleaner = Cleaner([BIN, cache], recycle_bin=self.bin)
            plan = cleaner.scan([BIN.id, cache.id])
            result = cleaner.clean(plan, [cache.id])
            self.assertFalse(owned.exists())
            self.assertEqual(result.deleted_files, 1)
            self.assertEqual(self.bin.empty_calls, 0)

    def test_service_displays_changed_bin_instead_of_claiming_success(self):
        with TemporaryDirectory(prefix="clearspace-bin-notice-test-") as directory:
            service = CleanerService(self.cleaner, directory)
            try:
                service.set_selection([BIN.id])
                service.scan()
                state = self.wait(service, "ready")
                self.bin.snapshot = RecycleBinSnapshot(4, 700)
                service.clean(state["scan_id"], [BIN.id])
                result = self.wait(service, "completed")
                self.assertEqual((self.bin.empty_calls, result["result"]["removed"]), (0, 0))
                self.assertIn("changed since analysis", result["error"])
            finally:
                service.shutdown()

    @staticmethod
    def wait(service, phase):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = service.get_state()
            if state["phase"] == phase:
                return state
            time.sleep(0.005)
        raise AssertionError(service.get_state())
