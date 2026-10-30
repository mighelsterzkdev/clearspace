"""Elevation contract tests mock Windows completely and never display UAC."""

from __future__ import annotations

import ctypes
from pathlib import Path
import runpy
import sys
import tempfile
from unittest.mock import MagicMock, patch
import unittest

from clearspace import elevation, gui
from clearspace.elevation import ElevationError, relaunch_as_administrator


class ElevationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="clearspace-elevation-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.python = self.root / "python.exe"
        self.pythonw = self.root / "pythonw.exe"
        self.frozen = self.root / "Clearspace app.exe"
        for path in (self.python, self.pythonw, self.frozen):
            path.write_bytes(b"owned non-executable test fixture")
        self.argv_patch = patch.object(elevation.sys, "argv", ["clearspace"])
        self.argv_patch.start()
        self.addCleanup(self.argv_patch.stop)

    def test_structure_matches_windows_64_bit_abi(self):
        if ctypes.sizeof(ctypes.c_void_p) != 8:
            self.skipTest("This assertion targets the Windows x64 package.")
        self.assertEqual(ctypes.sizeof(elevation._SHELLEXECUTEINFOW), 112)
        self.assertEqual(elevation._SHELLEXECUTEINFOW.lpVerb.offset, 16)
        self.assertEqual(elevation._SHELLEXECUTEINFOW.nShow.offset, 48)
        self.assertEqual(elevation._SHELLEXECUTEINFOW.hProcess.offset, 104)

    def test_source_relaunch_uses_own_windowless_isolated_module_only(self):
        request = MagicMock(return_value=0)
        with patch.object(elevation.sys, "executable", str(self.python)), \
                patch.object(elevation.sys, "frozen", False, create=True), \
                patch.object(elevation.sys, "argv", ["clearspace", "--anything", r"C:\unrelated.exe"]):
            self.assertTrue(relaunch_as_administrator(request=request))
        command = request.call_args.args[0]
        self.assertEqual(command.executable, str(self.pythonw.resolve()))
        self.assertEqual(command.arguments, ("-I", "-B", "-m", "clearspace.gui", "--clearspace-elevated"))
        self.assertEqual(command.directory, str(self.pythonw.parent.resolve()))
        self.assertNotIn("--anything", command.arguments)
        self.assertNotIn(r"C:\unrelated.exe", command.arguments)

    def test_frozen_relaunch_uses_own_executable_and_fixed_marker(self):
        request = MagicMock(return_value=0)
        with patch.object(elevation.sys, "executable", str(self.frozen)), \
                patch.object(elevation.sys, "frozen", True, create=True), \
                patch.object(elevation.sys, "argv", ["clearspace", "--arbitrary-path", "anything"]):
            self.assertTrue(relaunch_as_administrator(request=request))
        command = request.call_args.args[0]
        self.assertEqual(command.executable, str(self.frozen.resolve()))
        self.assertEqual(command.arguments, ("--clearspace-elevated",))
        self.assertEqual(command.directory, str(self.frozen.parent.resolve()))

    def test_uac_cancel_keeps_the_current_app_without_retry(self):
        request = MagicMock(return_value=1223)
        with patch.object(elevation.sys, "executable", str(self.frozen)), \
                patch.object(elevation.sys, "frozen", True, create=True):
            self.assertFalse(relaunch_as_administrator(request=request))
        request.assert_called_once()

    def test_missing_windowless_source_runtime_does_not_prompt_uac(self):
        self.pythonw.unlink()
        request = MagicMock()
        with patch.object(elevation.sys, "executable", str(self.python)), \
                patch.object(elevation.sys, "frozen", False, create=True):
            with self.assertRaisesRegex(ElevationError, "runtime is missing"):
                relaunch_as_administrator(request=request)
        request.assert_not_called()

    def test_relative_runtime_path_is_rejected(self):
        request = MagicMock()
        with patch.object(elevation.sys, "executable", "python.exe"):
            with self.assertRaisesRegex(ElevationError, "locate"):
                relaunch_as_administrator(request=request)
        request.assert_not_called()

    def test_native_launch_error_is_bounded_and_preserves_error_code(self):
        with patch.object(elevation.sys, "executable", str(self.frozen)), \
                patch.object(elevation.sys, "frozen", True, create=True):
            with self.assertRaisesRegex(ElevationError, "code 5") as caught:
                relaunch_as_administrator(request=lambda command: 5)
        self.assertNotIn(str(self.root), str(caught.exception))

    def test_native_load_failure_has_clear_message_without_raw_exception(self):
        with patch.object(elevation.sys, "executable", str(self.frozen)), \
                patch.object(elevation.sys, "frozen", True, create=True):
            with self.assertRaisesRegex(ElevationError, "could not request") as caught:
                relaunch_as_administrator(request=MagicMock(side_effect=OSError("private native detail")))
        self.assertNotIn("private native detail", str(caught.exception))

    def native_apis(self, *, initialize=0, success=True, error=0):
        ole = MagicMock()
        ole.CoInitializeEx.return_value = initialize
        shell = MagicMock()
        captured = []

        def execute(pointer):
            info = ctypes.cast(pointer, ctypes.POINTER(elevation._SHELLEXECUTEINFOW)).contents
            captured.append({name: getattr(info, name) for name in (
                "cbSize", "fMask", "hwnd", "lpVerb", "lpFile", "lpParameters", "lpDirectory", "nShow", "hProcess")})
            ctypes.set_last_error(error)
            return success

        shell.ShellExecuteExW.side_effect = execute
        return shell, ole, captured

    def test_native_request_uses_runas_synchronous_unicode_and_no_extra_failure_dialog(self):
        shell, ole, captured = self.native_apis()
        command = elevation._RelaunchCommand(str(self.frozen), ("--clearspace-elevated",), str(self.root))
        self.assertEqual(elevation._request_elevation(command, shell=shell, ole=ole), 0)
        info = captured[0]
        self.assertEqual(info["cbSize"], 112)
        self.assertEqual(info["fMask"], 0x4500)
        self.assertEqual(info["lpVerb"], "runas")
        self.assertEqual(info["lpFile"], str(self.frozen))
        self.assertEqual(info["lpParameters"], "--clearspace-elevated")
        self.assertEqual(info["lpDirectory"], str(self.root))
        self.assertEqual(info["nShow"], 1)
        self.assertIsNone(info["hwnd"])
        self.assertIsNone(info["hProcess"])
        ole.CoInitializeEx.assert_called_once_with(None, 6)
        ole.CoUninitialize.assert_called_once_with()

    def test_native_cancel_error_is_returned_without_retry(self):
        shell, ole, captured = self.native_apis(success=False, error=1223)
        command = elevation._RelaunchCommand(str(self.frozen), (), str(self.root))
        self.assertEqual(elevation._request_elevation(command, shell=shell, ole=ole), 1223)
        self.assertEqual(len(captured), 1)
        ole.CoUninitialize.assert_called_once_with()

    def test_existing_com_sta_reference_is_balanced(self):
        shell, ole, _ = self.native_apis(initialize=1)
        command = elevation._RelaunchCommand(str(self.frozen), (), str(self.root))
        elevation._request_elevation(command, shell=shell, ole=ole)
        ole.CoUninitialize.assert_called_once_with()

    def test_existing_other_com_apartment_is_not_uninitialized(self):
        shell, ole, _ = self.native_apis(initialize=ctypes.c_int32(0x80010106).value)
        command = elevation._RelaunchCommand(str(self.frozen), (), str(self.root))
        elevation._request_elevation(command, shell=shell, ole=ole)
        ole.CoUninitialize.assert_not_called()

    def test_failed_com_initialization_does_not_request_uac(self):
        shell, ole, _ = self.native_apis(initialize=ctypes.c_int32(0x80004005).value)
        command = elevation._RelaunchCommand(str(self.frozen), (), str(self.root))
        with self.assertRaisesRegex(ElevationError, "prepare"):
            elevation._request_elevation(command, shell=shell, ole=ole)
        shell.ShellExecuteExW.assert_not_called()
        ole.CoUninitialize.assert_not_called()

    def test_gui_opens_without_requesting_elevation(self):
        with patch.object(elevation, "_request_elevation") as request, \
                patch.object(gui, "_enable_per_monitor_dpi") as dpi, \
                patch.object(gui, "ASSETS", self.root / "absent-assets"), \
                patch.object(gui, "_fatal") as fatal:
            self.assertEqual(gui.main(), 1)
        request.assert_not_called()
        dpi.assert_called_once_with()
        self.assertIn("interface is missing", fatal.call_args.args[0])

    def test_restart_closes_the_window_only_after_a_launch(self):
        service, window = MagicMock(), MagicMock()
        api = gui.DesktopApi(service)
        api.attach(window)
        service.active.return_value = False
        with patch.object(gui, "relaunch_as_administrator", return_value=False):
            self.assertEqual(api.restart_as_administrator(), {"ok": True, "state": service.get_state.return_value})
        window.destroy.assert_not_called()
        with patch.object(gui, "relaunch_as_administrator", return_value=True):
            self.assertTrue(api.restart_as_administrator()["ok"])
        window.destroy.assert_called_once_with()

    def test_restart_waits_for_running_work(self):
        service = MagicMock()
        service.active.return_value = True
        api = gui.DesktopApi(service)
        with patch.object(gui, "relaunch_as_administrator") as relaunch:
            self.assertFalse(api.restart_as_administrator()["ok"])
        relaunch.assert_not_called()

    def test_portable_self_check_bypasses_gui_and_elevation(self):
        entry = Path(__file__).resolve().parents[1] / "scripts" / "gui_entry.py"
        with patch.object(sys, "argv", ["clearspace.exe", "--self-check"]), \
                patch("clearspace.diagnostics.verify_portable") as verify, \
                patch.object(gui, "main") as main, \
                patch.object(elevation, "_request_elevation") as ensure:
            with self.assertRaises(SystemExit) as caught:
                runpy.run_path(str(entry), run_name="__main__")
        self.assertEqual(caught.exception.code, 0)
        verify.assert_called_once_with()
        main.assert_not_called()
        ensure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
