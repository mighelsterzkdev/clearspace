"""Native cleanup authorization and accounting with fake providers only."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from clearspace.engine import Category, Cleaner
from clearspace.maintenance import ActionResult, ActionSnapshot, MaintenanceBusyError, MaintenanceStillRunningError
from clearspace.service import CleanerService, ServiceError


ACTION = Category("windows_components", "Windows update cleanup", "Native cleanup", (),
                  native_action="windows_components")


class FakeAction:
    def __init__(self, pending=True, size=None):
        self.snapshot = ActionSnapshot(pending, size, "Windows manages this cleanup.")
        self.result = ActionResult(True, None, "Windows completed cleanup; size unavailable.")
        self.calls = []
        self.query_error = False
        self.clean_error = False

    def query(self, cancel=None):
        if self.query_error:
            raise OSError("Administrator permission required.")
        return self.snapshot

    def clean(self, snapshot, cancel=None):
        self.calls.append(snapshot)
        if self.clean_error:
            raise OSError("Native cleanup failed.")
        return self.result


class NativeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.provider = FakeAction()
        self.cleaner = Cleaner([ACTION], native_actions={ACTION.id: self.provider})

    def test_unknown_size_zero_file_action_remains_cleanable_and_not_fabricated(self):
        plan = self.cleaner.scan([ACTION.id])
        self.assertEqual((plan.total_files, plan.total_bytes, plan.total_actions), (0, 0, 1))
        self.assertEqual(plan.category_stats[0].actions, 1)
        self.assertFalse(plan.category_stats[0].size_known)
        self.assertFalse(self.provider.calls)
        result = self.cleaner.clean(plan, [ACTION.id])
        self.assertEqual((result.deleted_files, result.freed_bytes), (0, 0))
        self.assertEqual(result.action_results[0]["status"], "completed")
        self.assertIsNone(result.action_results[0]["freed_bytes"])
        self.assertIs(self.provider.calls[0], plan.native_actions[0].snapshot)

    def test_forged_expired_and_consumed_plans_cannot_run_native_actions(self):
        plan = self.cleaner.scan([ACTION.id])
        with self.assertRaises(ValueError):
            self.cleaner.clean(replace(plan), [ACTION.id])
        with patch("clearspace.engine.time.time", return_value=plan.expires_at + 1):
            with self.assertRaises(ValueError):
                self.cleaner.clean(plan, [ACTION.id])
        self.assertFalse(self.provider.calls)
        plan = self.cleaner.scan([ACTION.id])
        self.cleaner.clean(plan, [ACTION.id])
        with self.assertRaises(ValueError):
            self.cleaner.clean(plan, [ACTION.id])
        self.assertEqual(len(self.provider.calls), 1)

    def test_cancelled_or_truncated_analysis_never_runs_actions(self):
        cancel = threading.Event()
        cancel.set()
        plan = self.cleaner.scan([ACTION.id], cancel=cancel)
        with self.assertRaises(ValueError):
            self.cleaner.clean(plan, [ACTION.id])
        self.assertFalse(self.provider.calls)

    def test_cancellation_skips_native_action(self):
        plan = self.cleaner.scan([ACTION.id])
        cancel = threading.Event()
        cancel.set()
        result = self.cleaner.clean(plan, [ACTION.id], cancel=cancel)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.action_results[0]["status"], "skipped")
        self.assertFalse(self.provider.calls)

    def test_query_failure_is_visible_and_does_not_authorize_action(self):
        self.provider.query_error = True
        plan = self.cleaner.scan([ACTION.id])
        self.assertEqual(plan.total_actions, 0)
        self.assertIn("Administrator", plan.category_stats[0].reason)
        self.assertTrue(plan.category_stats[0].analysis_error)
        result = self.cleaner.clean(plan, [ACTION.id])
        self.assertEqual(result.action_results, ())
        self.assertFalse(self.provider.calls)

    def test_already_clean_category_does_not_run_native_action(self):
        self.provider.snapshot = ActionSnapshot(False, None, "Nothing needs cleaning.")
        plan = self.cleaner.scan([ACTION.id])
        result = self.cleaner.clean(plan, [ACTION.id])
        self.assertEqual(plan.total_actions, 0)
        self.assertEqual(result.action_results, ())
        self.assertFalse(self.provider.calls)

    def test_native_failure_preserves_successful_file_accounting(self):
        with TemporaryDirectory(prefix="clearspace-native-cache-test-") as directory:
            folder = Path(directory)
            marker = folder / "owned.tmp"
            marker.write_bytes(b"owned cache")
            cache = Category("cache", "Cache", "Owned", (folder,))
            self.provider.clean_error = True
            cleaner = Cleaner([ACTION, cache], native_actions={ACTION.id: self.provider})
            plan = cleaner.scan([ACTION.id, cache.id])
            result = cleaner.clean(plan, [ACTION.id, cache.id])
            self.assertFalse(marker.exists())
            self.assertEqual((result.deleted_files, result.freed_bytes), (1, len(b"owned cache")))
            self.assertEqual(result.action_results[0]["status"], "failed")
            self.assertIn("Native cleanup failed", result.action_results[0]["detail"])

    def test_late_native_query_does_not_exhaust_file_analysis_budget(self):
        with TemporaryDirectory(prefix="clearspace-native-budget-test-") as directory:
            folder = Path(directory)
            marker = folder / "owned.tmp"
            marker.write_bytes(b"owned cache")
            cache = Category("cache", "Cache", "Owned", (folder,))
            clock = [0.0]

            def late_query(cancel=None):
                clock[0] += 69
                return self.provider.snapshot

            self.provider.query = late_query
            cleaner = Cleaner([ACTION, cache], native_actions={ACTION.id: self.provider}, max_scan_seconds=1)
            with patch("clearspace.engine.time.monotonic", side_effect=lambda: clock[0]):
                plan = cleaner.scan([ACTION.id, cache.id])
            self.assertFalse(plan.truncated)
            self.assertEqual((plan.total_files, plan.total_actions), (1, 1))
            self.assertTrue(marker.exists())

    def test_unfinished_native_analysis_is_pending_and_has_no_cleanup_authority(self):
        def pending_query(cancel=None):
            raise MaintenanceStillRunningError("Windows is still analyzing cleanup options. No cleanup was started.",
                                               action_id=ACTION.id, operation="analyze")

        self.provider.query = pending_query
        plan = self.cleaner.scan([ACTION.id])
        self.assertTrue(plan.category_stats[0].analysis_pending)
        self.assertFalse(plan.category_stats[0].analysis_error)
        self.assertEqual(plan.total_actions, 0)
        result = self.cleaner.clean(plan, [ACTION.id])
        self.assertEqual(result.action_results[0]["status"], "deferred")
        self.assertIn("analyzing", result.action_results[0]["detail"])
        self.assertFalse(self.provider.calls)

    def test_native_queue_wait_is_pending_instead_of_cleanup_failure(self):
        plan = self.cleaner.scan([ACTION.id])

        def queued_cleanup(snapshot, cancel=None):
            raise MaintenanceBusyError("Earlier Windows work is still running; this action has not started.")

        self.provider.clean = queued_cleanup
        result = self.cleaner.clean(plan, [ACTION.id])
        self.assertEqual(result.action_results[0]["status"], "deferred")
        self.assertEqual(result.failed_files, 0)
        self.assertEqual(result.deleted_files, 0)

    def test_deferred_service_result_does_not_increment_failed_actions(self):
        with TemporaryDirectory(prefix="clearspace-native-pending-test-") as directory:
            service = CleanerService(self.cleaner, directory)
            try:
                service.set_selection([ACTION.id])
                service.scan()
                ready = self.wait(service, "ready")

                def deferred_cleanup(snapshot, cancel=None):
                    raise MaintenanceBusyError("Earlier analysis is still running; this cleanup has not started.")

                self.provider.clean = deferred_cleanup
                service.clean(ready["scan_id"], [ACTION.id])
                result = self.wait(service, "completed")["result"]
                self.assertEqual((result["actions_completed"], result["actions_failed"], result["actions_deferred"]), (0, 0, 1))
            finally:
                service.shutdown()

    def test_service_progress_describes_running_analysis_without_claiming_cleanup(self):
        with TemporaryDirectory(prefix="clearspace-native-progress-test-") as directory:
            service = CleanerService(self.cleaner, directory)
            try:
                activity = {"action_id": ACTION.id, "operation": "analyze", "phase": "running", "elapsed_seconds": 68}
                service._state.update(phase="scanning", progress={"checked": 0, "files": 0, "bytes": 0, "current": ACTION.id})
                with patch("clearspace.maintenance.maintenance_activity", return_value=activity):
                    state = service.get_state()
                self.assertEqual(state["maintenance"]["operation"], "analyze")
                self.assertIn("analysis", state["progress"]["message"])
                state["maintenance"]["phase"] = "changed-copy"
                self.assertEqual(activity["phase"], "running")
            finally:
                service.shutdown()

    def test_unselected_action_does_not_run(self):
        other = replace(ACTION, id="delivery_optimization", native_action="delivery_optimization")
        other_provider = FakeAction()
        cleaner = Cleaner([ACTION, other], native_actions={ACTION.id: self.provider, other.id: other_provider})
        plan = cleaner.scan([ACTION.id, other.id])
        cleaner.clean(plan, [other.id])
        self.assertFalse(self.provider.calls)
        self.assertEqual(len(other_provider.calls), 1)

    def test_native_only_service_accepts_plan_and_reports_action_result(self):
        with TemporaryDirectory(prefix="clearspace-native-service-test-") as directory:
            service = CleanerService(self.cleaner, directory)
            try:
                service.set_selection([ACTION.id])
                service.scan()
                ready = self.wait(service, "ready")
                self.assertEqual(ready["categories"][0]["actions"], 1)
                with self.assertRaises(ServiceError):
                    service.clean("forged", [ACTION.id])
                service.clean(ready["scan_id"], [ACTION.id])
                result = self.wait(service, "completed")["result"]
                self.assertEqual((result["removed"], result["freed_bytes"], result["actions_completed"]), (0, 0, 1))
                self.assertEqual(result["actions_failed"], 0)
            finally:
                service.shutdown()

    def test_settings_bridge_only_opens_fixed_windows_uri(self):
        with TemporaryDirectory(prefix="clearspace-search-settings-test-") as directory:
            service = CleanerService(self.cleaner, directory)
            try:
                with patch("clearspace.service.os.startfile") as launch:
                    service.open_search_settings()
                launch.assert_called_once_with("ms-settings:search-permissions")
                self.assertFalse(self.provider.calls)
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
