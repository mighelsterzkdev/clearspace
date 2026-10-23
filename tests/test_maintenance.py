"""Native maintenance tests use fixed fake commands; no real cleanup runs."""

from __future__ import annotations

import base64
from dataclasses import FrozenInstanceError
from pathlib import Path
import subprocess
from threading import Event
import unittest
from unittest.mock import MagicMock, patch

from clearspace import maintenance
from clearspace.maintenance import (
    ActionResult, ActionSnapshot, MaintenanceError, WindowsComponentCleanup,
    WindowsDeliveryOptimization,
)


class FakeRunner:
    def __init__(self, *results, after_run=None):
        self.results = list(results)
        self.calls = []
        self.after_run = after_run

    def run(self, command, **options):
        self.calls.append((command, options))
        result = self.results.pop(0)
        if self.after_run:
            self.after_run()
        return result


def command_result(code=0, output=""):
    return maintenance._CommandResult(code, output)


def component_report(recommended="Yes"):
    return command_result(output=(
        "Actual Size of Component Store : 12.88 GB\r\n"
        "Backups and Disabled Features : 4.11 GB\r\n"
        "Number of Reclaimable Packages : 2\r\n"
        f"Component Store Cleanup Recommended : {recommended}\r\n"
    ))


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.path_patch = patch.object(maintenance.windows, "windows_directory",
                                      return_value=Path(r"C:\Windows"))
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    def components(self, runner, **kwargs):
        return WindowsComponentCleanup(runner=runner, administrator=lambda: True,
                                       restart_pending=kwargs.get("restart_pending", lambda: False))

    def delivery(self, runner):
        return WindowsDeliveryOptimization(runner=runner, administrator=lambda: True)

    def test_snapshots_and_results_are_immutable_and_keep_unknown_sizes(self):
        snapshot = ActionSnapshot(True)
        result = ActionResult(True)
        self.assertIsNone(snapshot.bytes)
        self.assertIsNone(result.freed_bytes)
        with self.assertRaises(FrozenInstanceError):
            snapshot.pending = False
        for constructor, values in ((ActionSnapshot, (1, None)), (ActionSnapshot, (True, -1)),
                                    (ActionSnapshot, (True, 1.1)), (ActionResult, (True, True)),
                                    (ActionResult, (0, None))):
            with self.subTest(constructor=constructor, values=values), self.assertRaises(ValueError):
                constructor(*values)

    def test_standard_user_cannot_query_or_start_native_commands(self):
        runner = FakeRunner()
        providers = (
            WindowsComponentCleanup(runner=runner, administrator=lambda: False),
            WindowsDeliveryOptimization(runner=runner, administrator=lambda: False),
        )
        for provider in providers:
            with self.subTest(provider=provider), self.assertRaisesRegex(MaintenanceError, "Administrator permission"):
                provider.query()
            with self.subTest(provider=provider), self.assertRaisesRegex(MaintenanceError, "Administrator permission"):
                provider.clean(ActionSnapshot(True))
        self.assertEqual(runner.calls, [])

    def test_component_query_uses_fixed_dism_report_without_guessing_reclaimable_size(self):
        runner = FakeRunner(component_report())
        snapshot = self.components(runner).query()
        self.assertTrue(snapshot.pending)
        self.assertIsNone(snapshot.bytes)
        command, options = runner.calls[0]
        self.assertEqual(command, (r"C:\Windows\System32\dism.exe", "/Online", "/Cleanup-Image",
                                   "/AnalyzeComponentStore", "/English", "/NoRestart"))
        self.assertEqual(options, {"timeout": maintenance.QUERY_TIMEOUT})

    def test_component_query_no_recommendation_does_not_schedule_action(self):
        runner = FakeRunner(component_report("No"))
        self.assertFalse(self.components(runner).query().pending)

    def test_unrecognized_component_report_is_not_authorized(self):
        for result in (command_result(output="Size : 100 GB"), command_result(87), command_result(3010)):
            with self.subTest(result=result), self.assertRaises(MaintenanceError):
                self.components(FakeRunner(result)).query()

    def test_component_pending_restart_blocks_native_query_and_cleanup(self):
        runner = FakeRunner()
        provider = self.components(runner, restart_pending=lambda: True)
        with self.assertRaisesRegex(MaintenanceError, "Restart Windows"):
            provider.query()
        with self.assertRaisesRegex(MaintenanceError, "Restart Windows"):
            provider.clean(ActionSnapshot(True))
        self.assertEqual(runner.calls, [])

    def test_component_clean_rechecks_then_uses_no_resetbase_or_restart(self):
        runner = FakeRunner(component_report(), command_result())
        result = self.components(runner).clean(ActionSnapshot(True))
        self.assertTrue(result.completed)
        self.assertIsNone(result.freed_bytes)
        self.assertEqual(len(runner.calls), 2)
        command, options = runner.calls[-1]
        self.assertIn("/StartComponentCleanup", command)
        self.assertIn("/NoRestart", command)
        self.assertNotIn("/ResetBase", command)
        self.assertEqual(options["timeout"], maintenance.COMPONENT_CLEAN_TIMEOUT)

    def test_component_cleanup_no_longer_recommended_is_not_run(self):
        runner = FakeRunner(component_report("No"))
        result = self.components(runner).clean(ActionSnapshot(True))
        self.assertTrue(result.completed)
        self.assertEqual(result.freed_bytes, 0)
        self.assertEqual(len(runner.calls), 1)

    def test_component_cleanup_success_reboot_and_failure_codes(self):
        result = self.components(FakeRunner(component_report(), command_result(3010))).clean(ActionSnapshot(True))
        self.assertTrue(result.completed)
        self.assertIn("Restart Windows", result.detail)
        with self.assertRaisesRegex(MaintenanceError, "failed.*87"):
            self.components(FakeRunner(component_report(), command_result(87))).clean(ActionSnapshot(True))

    def test_pre_cancelled_actions_never_run_commands(self):
        cancel = Event()
        cancel.set()
        runner = FakeRunner()
        for provider in (self.components(runner), self.delivery(runner)):
            result = provider.clean(ActionSnapshot(True), cancel)
            self.assertFalse(result.completed)
            self.assertIn("Stopped", result.detail)
        self.assertEqual(runner.calls, [])

    def test_cancellation_during_preflight_prevents_mutating_command(self):
        cancel = Event()
        runner = FakeRunner(component_report(), after_run=cancel.set)
        self.assertFalse(self.components(runner).clean(ActionSnapshot(True), cancel).completed)
        self.assertEqual(len(runner.calls), 1)
        cancel.clear()
        runner = FakeRunner(command_result(output='{"bytes":200}'), after_run=cancel.set)
        self.assertFalse(self.delivery(runner).clean(ActionSnapshot(True), cancel).completed)
        self.assertEqual(len(runner.calls), 1)

    def test_stop_after_native_start_does_not_misreport_completed_work_as_cancelled(self):
        cancel = Event()
        count = 0

        def after_run():
            nonlocal count
            count += 1
            if count == 2:
                cancel.set()

        runner = FakeRunner(component_report(), command_result(), after_run=after_run)
        result = self.components(runner).clean(ActionSnapshot(True), cancel)
        self.assertTrue(cancel.is_set())
        self.assertTrue(result.completed)

    def test_delivery_query_uses_fixed_encoded_noninteractive_module_script(self):
        runner = FakeRunner(command_result(output='{"bytes":1073741824}'))
        snapshot = self.delivery(runner).query()
        self.assertEqual(snapshot.bytes, 1073741824)
        self.assertTrue(snapshot.pending)
        self.assertIn("Pinned files are kept", snapshot.detail)
        command, options = runner.calls[0]
        self.assertEqual(command[0], r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
        self.assertEqual(command[1:-1], ("-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand"))
        script = base64.b64decode(command[-1]).decode("utf-16-le")
        self.assertIn("$PSHOME", script)
        self.assertIn("-DisableNameChecking", script)
        self.assertIn("$WarningPreference = 'SilentlyContinue'", script)
        self.assertIn("DeliveryOptimization\\Get-DeliveryOptimizationPerfSnap", script)
        self.assertNotIn("Delete-DeliveryOptimizationCache -Force", script)
        self.assertNotIn("ExecutionPolicy", script)
        self.assertEqual(options, {"timeout": maintenance.QUERY_TIMEOUT, "powershell": True})

    def test_delivery_unknown_size_stays_unknown_and_empty_cache_is_not_pending(self):
        unknown = self.delivery(FakeRunner(command_result(output='{"bytes":null}'))).query()
        empty = self.delivery(FakeRunner(command_result(output='{"bytes":0}'))).query()
        self.assertTrue(unknown.pending)
        self.assertIsNone(unknown.bytes)
        self.assertFalse(empty.pending)
        self.assertEqual(empty.bytes, 0)

    def test_delivery_invalid_reports_are_rejected(self):
        for value in ('null', '{}', '[]', '{"bytes":-2}', '{"bytes":false}', '{"bytes":0.1}', 'not json'):
            with self.subTest(value=value), self.assertRaises(MaintenanceError):
                self.delivery(FakeRunner(command_result(output=value))).query()

    def test_delivery_module_service_errors_are_reported(self):
        with self.assertRaisesRegex(MaintenanceError, "could not be read"):
            self.delivery(FakeRunner(command_result(1))).query()
        with self.assertRaisesRegex(MaintenanceError, "could not complete"):
            self.delivery(FakeRunner(command_result(output='{"bytes":20}'), command_result(1))).clean(ActionSnapshot(True))

    def test_delivery_clean_skips_own_prompt_only_and_keeps_pinned_files(self):
        runner = FakeRunner(command_result(output='{"bytes":20}'), command_result())
        result = self.delivery(runner).clean(ActionSnapshot(True, 10))
        self.assertTrue(result.completed)
        self.assertIsNone(result.freed_bytes)
        command, options = runner.calls[-1]
        script = base64.b64decode(command[-1]).decode("utf-16-le")
        self.assertIn("Delete-DeliveryOptimizationCache -Force -ErrorAction Stop", script)
        self.assertNotIn("-IncludePinnedFiles", script)
        self.assertNotIn("Remove-Item", script)
        self.assertEqual(options["timeout"], maintenance.DELIVERY_CLEAN_TIMEOUT)

    def test_already_empty_native_snapshot_does_not_start_commands(self):
        runner = FakeRunner()
        for provider in (self.components(runner), self.delivery(runner)):
            result = provider.clean(ActionSnapshot(False))
            self.assertTrue(result.completed)
            self.assertEqual(result.freed_bytes, 0)
        self.assertEqual(runner.calls, [])

    def test_invalid_snapshot_and_arbitrary_native_operations_are_rejected(self):
        for provider in (self.components(FakeRunner()), self.delivery(FakeRunner())):
            with self.subTest(provider=provider), self.assertRaises(MaintenanceError):
                provider.clean({"pending": True})
        with self.assertRaises(ValueError):
            self.components(FakeRunner())._command("/ResetBase")
        with self.assertRaises(ValueError):
            self.delivery(FakeRunner())._run("Remove-Item C:\\*", 1)

    def test_runner_does_not_use_shell_or_search_path_and_hides_native_windows(self):
        child = MagicMock(returncode=0)
        child.wait.return_value = 0
        runner = maintenance._CommandRunner()
        with patch.object(maintenance.subprocess, "Popen", return_value=child) as start:
            runner.run((r"C:\Windows\System32\dism.exe", "/Online"), timeout=5)
        _, options = start.call_args
        self.assertFalse(options["shell"])
        self.assertTrue(options["close_fds"])
        self.assertEqual(options["cwd"], Path(r"C:\Windows\System32"))
        self.assertEqual(options["creationflags"], subprocess.CREATE_NO_WINDOW)
        self.assertEqual(options["startupinfo"].wShowWindow, subprocess.SW_HIDE)
        self.assertEqual(options["stdin"], subprocess.DEVNULL)

    def test_runner_limits_powershell_modules_to_windows_modules(self):
        child = MagicMock(returncode=0)
        runner = maintenance._CommandRunner()
        with patch.object(maintenance.subprocess, "Popen", return_value=child) as start:
            runner.run((r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",), timeout=5, powershell=True)
        self.assertEqual(start.call_args.kwargs["env"]["PSModulePath"],
                         r"C:\Windows\System32\WindowsPowerShell\v1.0\Modules")

    def test_timeout_never_terminates_windows_servicing_and_prevents_overlapping_work(self):
        child = MagicMock()
        child.wait.side_effect = subprocess.TimeoutExpired("dism.exe", 5)
        watcher = MagicMock()
        runner = maintenance._CommandRunner()
        with patch.object(maintenance.subprocess, "Popen", return_value=child), \
                patch.object(maintenance, "Thread", return_value=watcher) as thread_start:
            with self.assertRaisesRegex(MaintenanceError, "still running"):
                runner.run((r"C:\Windows\System32\dism.exe", "/Online"), timeout=5)
            with self.assertRaisesRegex(MaintenanceError, "still running"):
                runner.run((r"C:\Windows\System32\dism.exe", "/Online"), timeout=5)
        child.kill.assert_not_called()
        child.terminate.assert_not_called()
        watcher.start.assert_called_once()
        # Close the owned fake process output since the mocked reaper did not run.
        child.wait.side_effect = None
        runner._reap(child, thread_start.call_args.kwargs["args"][1])
        self.assertIsNone(runner._active)


if __name__ == "__main__":
    unittest.main()
