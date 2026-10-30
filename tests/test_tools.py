"""Maintenance tools: parsers, validation and startup switches without touching real settings."""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import time
import unittest

from clearspace import tools

WINGET = (
    "   - \r   \\ \r"
    "Name                                Id                           Version       Available     Source\r\n"
    "---------------------------------------------------------------------------------------------------\r\n"
    "7-Zip 26.04 (x64)                   7zip.7zip                    26.04         26.05         winget\r\n"
    "Microsoft Visual C++ 2015-2022 R…   Microsoft.VCRedist.2015+.x64 14.44.35211.0 14.51.36247.0 winget\r\n"
    "CapCut                              XP9KN75RRB9NHS               9.7.0.4151    9.7.0.4153    msstore\r\n"
    "3 upgrades available.\r\n"
    "\r\n"
    "The following packages have an upgrade available, but require explicit targeting for upgrade:\r\n"
    "Name     Id          Version Available Source\r\n"
    "----------------------------------------------\r\n"
    "Pinned   Some.Pinned 1.0     2.0       winget\r\n"
)


def wait(service, tool, *statuses):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = service.snapshot()
        if state[tool]["status"] in statuses:
            return state
        time.sleep(0.01)
    raise AssertionError(service.snapshot()[tool])


class ParserTests(unittest.TestCase):
    def test_winget_table_in_any_language_and_only_the_first_table(self):
        packages = tools.parse_winget_table(WINGET)
        self.assertEqual([package["id"] for package in packages], ["7zip.7zip", "Microsoft.VCRedist.2015+.x64", "XP9KN75RRB9NHS"])
        self.assertEqual(packages[0], {"id": "7zip.7zip", "name": "7-Zip 26.04 (x64)", "version": "26.04", "available": "26.05", "source": "winget", "status": "pending"})
        portuguese = WINGET.replace("Name                                Id                           Version       Available     Source",
                                    "Nome                                ID                           Versão        Disponível    Origem")
        self.assertEqual(len(tools.parse_winget_table(portuguese)), 3)
        self.assertEqual(tools.parse_winget_table("No installed package found matching input criteria."), [])

    def test_connection_verdicts(self):
        self.assertEqual(tools.summarize([11.0, 12.0, 11.0, 11.0])["verdict"], "stable")
        self.assertEqual(tools.summarize([10.0, 60.0, 12.0, 70.0, 11.0])["verdict"], "unstable")
        losing = tools.summarize([10.0] * 18 + [None, None])
        self.assertEqual((losing["verdict"], losing["loss"]), ("losing", 10.0))
        self.assertEqual(tools.summarize([None, None])["verdict"], "offline")
        self.assertIsNone(tools.summarize([])["verdict"])

    def test_progress_and_repair_findings(self):
        self.assertEqual(tools.last_percent("[====   18.4%   ]\r[=====  27,9%  ]"), 27.9)
        self.assertEqual(tools.last_percent("Verification 45% complete."), 45.0)
        self.assertIsNone(tools.last_percent("Beginning system scan."))
        since = datetime(2026, 10, 30, 12, 0, 0)
        log = [
            "2026-10-30 11:59:59, Info CSI [SR] Repairing corrupted file old",
            "2026-10-30 12:01:00, Info CSI [SR] Repairing corrupted file \\??\\C:\\Windows\\a.dll",
            "2026-10-30 12:01:01, Info CSI [SR] Cannot repair member file b.dll",
            "2026-10-30 12:02:00, Info CSI [SR] Verify complete",
            "not a log line [SR]",
        ]
        self.assertEqual(tools.sfc_findings(log, since), {"repaired": 1, "unrepaired": 1, "verified": True})

    def test_disk_health_rows(self):
        single = '{"Name":"KINGSTON SA400S37960G","Media":"SSD","Bus":"SATA","Health":"Healthy","Size":960197124096,"Temperature":null,"Wear":3}'
        self.assertEqual(tools.parse_disks(single), [{"name": "KINGSTON SA400S37960G", "media": "SSD", "bus": "SATA", "health": "healthy",
                                                     "size": 960197124096, "temperature": None, "wear": 3}])
        self.assertEqual(tools.parse_disks('[{"Name":"A","Health":"Strange"}]')[0]["health"], "unknown")
        self.assertEqual(tools.parse_disks("not json"), [])

    def test_startup_flags_and_names(self):
        self.assertTrue(tools.approved_enabled(None))
        self.assertTrue(tools.approved_enabled(tools.approved_value(True)))
        disabled = tools.approved_value(False, now=0)
        self.assertFalse(tools.approved_enabled(disabled))
        self.assertEqual(len(disabled), 12)
        self.assertEqual(tools.app_name("MicrosoftTeams_8wekyb3d8bbwe"), "Microsoft Teams")
        self.assertEqual(tools.app_name("Microsoft.WindowsTerminal_8wekyb3d8bbwe"), "Windows Terminal")
        self.assertEqual(tools._program('"C:\\Program Files\\App\\app.exe" --minimized'), "app.exe")
        self.assertEqual(tools._program("C:\\Tools\\tray.exe /background"), "tray.exe")


@unittest.skipUnless(os.name == "nt", "uses the Windows registry")
class StartupSourceTests(unittest.TestCase):
    """Runs against a throwaway registry key and folder, never the real startup settings."""

    def setUp(self):
        import winreg
        self.winreg = winreg
        self.base = rf"Software\ClearspaceTests\{os.getpid()}-{time.monotonic_ns()}"
        self.folders = TemporaryDirectory(prefix="clearspace-startup-test-")
        user = Path(self.folders.name) / "user"
        user.mkdir()
        (user / "Notes.lnk").write_bytes(b"x")
        (user / "desktop.ini").write_bytes(b"x")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{self.base}\user\{tools.RUN}") as key:
            winreg.SetValueEx(key, "Chat", 0, winreg.REG_SZ, '"C:\\Apps\\chat.exe" --tray')
            winreg.SetValueEx(key, "Bad", 0, winreg.REG_DWORD, 1)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{self.base}\user\{tools.APP_TASKS}\Vendor.Music_abc\MusicStartup") as key:
            winreg.SetValueEx(key, "State", 0, winreg.REG_DWORD, 2)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{self.base}\user\{tools.APP_TASKS}\Vendor.Locked_abc\Task") as key:
            winreg.SetValueEx(key, "State", 0, winreg.REG_DWORD, 4)
        self.sources = tools.StartupSources(
            user_root=(winreg.HKEY_CURRENT_USER, rf"{self.base}\user"), machine_root=(winreg.HKEY_CURRENT_USER, rf"{self.base}\machine"),
            user_folder=user, machine_folder=Path(self.folders.name) / "missing")

    def tearDown(self):
        def remove(path):
            try:
                with self.winreg.OpenKey(self.winreg.HKEY_CURRENT_USER, path) as key:
                    while True:
                        try:
                            child = self.winreg.EnumKey(key, 0)
                        except OSError:
                            break
                        remove(rf"{path}\{child}")
                self.winreg.DeleteKey(self.winreg.HKEY_CURRENT_USER, path)
            except FileNotFoundError:
                pass
        remove(self.base)
        try:
            self.winreg.DeleteKey(self.winreg.HKEY_CURRENT_USER, r"Software\ClearspaceTests")
        except OSError:
            pass
        self.folders.cleanup()

    def items(self):
        return {item["id"]: item for item in self.sources.items()}

    def test_lists_and_switches_entries_like_task_manager(self):
        items = self.items()
        self.assertEqual(set(items), {"user:Run:Chat", "user:StartupFolder:Notes.lnk", "user:App:Vendor.Music_abc\\MusicStartup", "user:App:Vendor.Locked_abc\\Task"})
        self.assertEqual(items["user:Run:Chat"]["program"], "chat.exe")
        self.assertTrue(all(item["enabled"] for item in items.values()))
        for identifier in ("user:Run:Chat", "user:StartupFolder:Notes.lnk", "user:App:Vendor.Music_abc\\MusicStartup"):
            self.sources.set_enabled(identifier, False)
            self.assertFalse(self.items()[identifier]["enabled"], identifier)
            self.sources.set_enabled(identifier, True)
            self.assertTrue(self.items()[identifier]["enabled"], identifier)
        # Disabling never deletes the entry itself.
        with self.winreg.OpenKey(self.winreg.HKEY_CURRENT_USER, rf"{self.base}\user\{tools.RUN}") as key:
            self.assertEqual(self.winreg.QueryValueEx(key, "Chat")[0], '"C:\\Apps\\chat.exe" --tray')

    def test_unknown_or_policy_items_are_refused(self):
        for identifier in ("user:Run:Missing", "user:Run:..\\..\\Other", "user:App:Vendor.Locked_abc\\Task"):
            with self.assertRaises(tools.ToolError):
                self.sources.set_enabled(identifier, False)


class ServiceTests(unittest.TestCase):
    def service(self, **kwargs):
        defaults = {"administrator": lambda: False, "echo": lambda address, timeout_ms=1000: 12.0,
                    "flush": lambda: None, "disks": lambda: [], "startup": None}
        return tools.ToolsService(**{**defaults, **kwargs})

    def test_connection_runs_only_for_known_targets_or_valid_names(self):
        service = self.service()
        for target in ("1.1.1.1; calc", "http://example.com", "", None, "a" * 300):
            with self.assertRaises(tools.ToolError):
                service.test_connection(target)
        original = tools.PING_SAMPLES, tools.PING_INTERVAL
        tools.PING_SAMPLES, tools.PING_INTERVAL = 3, 0
        try:
            service.test_connection("cloudflare")
            state = wait(service, "connection", "done")
        finally:
            tools.PING_SAMPLES, tools.PING_INTERVAL = original
        self.assertEqual(state["connection"]["samples"], [12.0, 12.0, 12.0])
        self.assertEqual(state["connection"]["summary"]["verdict"], "stable")

    def test_updates_install_only_listed_packages_one_at_a_time(self):
        calls = []

        def winget(*arguments, timeout):
            calls.append(arguments)
            return subprocess.CompletedProcess(arguments, 0 if arguments[0] == "upgrade" and "--id" not in arguments or arguments[2] == "7zip.7zip" else 1,
                                               WINGET.encode("utf-8"), b"")
        service = self.service(winget=winget)
        with self.assertRaises(tools.ToolError):
            service.install_updates(["7zip.7zip"])
        service.check_updates()
        wait(service, "updates", "ready")
        for chosen in (["Some.Pinned"], ["7zip.7zip", "7zip.7zip"], "7zip.7zip", []):
            with self.assertRaises(tools.ToolError):
                service.install_updates(chosen)
        service.install_updates(["7zip.7zip", "XP9KN75RRB9NHS"])
        state = wait(service, "updates", "done")
        statuses = {package["id"]: package["status"] for package in state["updates"]["packages"]}
        self.assertEqual(statuses, {"7zip.7zip": "updated", "Microsoft.VCRedist.2015+.x64": "pending", "XP9KN75RRB9NHS": "failed"})
        self.assertEqual([call[2] for call in calls[1:]], ["7zip.7zip", "XP9KN75RRB9NHS"])
        self.assertTrue(all("--silent" in call and "--exact" in call for call in calls[1:]))

    def test_repair_needs_administrator_and_reports_its_verdict(self):
        with self.assertRaises(tools.ToolError):
            self.service().repair()

        def runner(progress):
            progress("dism", 50.0)
            progress("sfc", 100.0)
            return {"verdict": "repaired", "repaired": 2, "unrepaired": 0, "verified": True, "dism_code": 0, "sfc_code": 0}
        service = self.service(administrator=lambda: True, repair_runner=runner)
        service.repair()
        self.assertTrue(service.busy() or service.snapshot()["repair"]["status"] == "done")
        state = wait(service, "repair", "done")
        self.assertEqual(state["repair"]["result"]["verdict"], "repaired")
        self.assertFalse(service.busy())

    def test_failures_are_reported_without_details(self):
        def broken():
            raise RuntimeError("C:\\secret\\path")
        service = self.service(flush=broken)
        service.flush_dns()
        state = wait(service, "dns", "failed")
        self.assertNotIn("secret", state["dns"]["error"])


if __name__ == "__main__":
    unittest.main()
