"""Catalog integration tests use only explicit, owned fake Windows folders."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from clearspace import catalog
from clearspace.engine import Cleaner


@unittest.skipUnless(os.name == "nt", "Windows catalog handle integration tests")
class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.TemporaryDirectory(prefix="clearspace-catalog-test-")
        self.addCleanup(self.sandbox.cleanup)
        self.directory = Path(self.sandbox.name)
        self.locations = {name: self.directory / name
                          for name in ("local", "roaming", "low", "common", "windows")}
        for path in self.locations.values():
            path.mkdir()

    def directory_at(self, location, relative):
        path = self.locations[location] / relative
        self.assertTrue(path.is_relative_to(self.directory))
        path.mkdir(parents=True, exist_ok=True)
        return path

    def file_at(self, location, relative, content=b"owned fixture"):
        path = self.locations[location] / relative
        self.assertTrue(path.is_relative_to(self.directory))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def categories(self):
        values = catalog.discover_categories(self.locations)
        self.assertEqual(len({item.id for item in values}), len(values))
        for category in values:
            for root in category.roots:
                self.assertTrue(root.is_relative_to(self.directory), root)
        return {item.id: item for item in values}

    def assert_not_targeted(self, categories, files):
        for path in files:
            for category in categories.values():
                for root in category.roots:
                    self.assertFalse(path == root or path.is_relative_to(root),
                                     f"Personal fixture {path} was covered by {category.id}: {root}")

    def junction(self, path, target):
        import _winapi
        self.assertTrue(path.is_relative_to(self.directory))
        self.assertTrue(target.is_relative_to(self.directory))
        path.parent.mkdir(parents=True, exist_ok=True)
        _winapi.CreateJunction(str(target), str(path))

    def test_injected_locations_do_not_resolve_real_known_folders(self):
        self.directory_at("local", "Temp")
        self.directory_at("windows", "Temp")
        with patch("clearspace.windows.local_app_data", side_effect=AssertionError("Real location used")), \
                patch("clearspace.windows.roaming_app_data", side_effect=AssertionError("Real location used")), \
                patch("clearspace.windows.local_low_app_data", side_effect=AssertionError("Real location used")), \
                patch("clearspace.windows.common_app_data", side_effect=AssertionError("Real location used")), \
                patch("clearspace.windows.windows_directory", side_effect=AssertionError("Real location used")):
            categories = self.categories()
        self.assertEqual(categories["user_temp"].roots, (self.locations["local"] / "Temp",))
        self.assertEqual(categories["windows_temp"].roots, (self.locations["windows"] / "Temp",))

    def test_absent_locations_are_unavailable_and_catalog_has_no_age_gate(self):
        categories = self.categories()
        self.assertGreaterEqual(len(categories), 20)
        for category in categories.values():
            self.assertEqual(category.minimum_age_days, 0, category.id)
            self.assertFalse(category.available, category.id)
            self.assertTrue(category.reason, category.id)
            self.assertIn(category.group, {"Windows", "Browsers", "Applications", "Developer tools"})
        for identifier in ("user_temp", "windows_temp", "crash_dumps", "error_reports"):
            self.assertTrue(categories[identifier].default_selected)
        for identifier in ("windows_cbs_logs", "windows_dism_logs", "thumbnail_cache",
                           "directx_cache", "nvidia_cache", "amd_cache", "pip_cache", "npm_cache", "nuget_cache"):
            self.assertFalse(categories[identifier].default_selected)

    def test_chromium_profile_names_and_cache_leaves_are_specific(self):
        base = self.locations["local"] / "Google/Chrome/User Data"
        expected = set()
        for name in ("Default", "Profile 0", "Profile 12"):
            for leaf in ("Cache", "Code Cache", "GPUCache"):
                expected.add(self.directory_at("local", f"Google/Chrome/User Data/{name}/{leaf}"))
        for name in ("Guest Profile", "Default-backup", "Profile x", "Profile 2 backup"):
            self.directory_at("local", f"Google/Chrome/User Data/{name}/Cache")
        personal = [
            self.file_at("local", "Google/Chrome/User Data/Default/Cookies"),
            self.file_at("local", "Google/Chrome/User Data/Default/Preferences"),
            self.file_at("local", "Google/Chrome/User Data/Default/Local Storage/leveldb/data.log"),
            self.file_at("local", "Google/Chrome/User Data/Default/IndexedDB/database.db"),
            self.file_at("local", "Google/Chrome/User Data/Default/Service Worker/CacheStorage/data"),
        ]
        categories = self.categories()
        chrome = categories["chrome_cache"]
        self.assertEqual(set(chrome.roots), expected)
        self.assertNotIn(base, chrome.roots)
        self.assertEqual(chrome.group, "Browsers")
        self.assertIn("chrome.exe", chrome.processes)
        self.assert_not_targeted(categories, personal)

    def test_profile_junctions_and_reparse_base_are_not_followed(self):
        base = self.locations["local"] / "Google/Chrome/User Data"
        base.mkdir(parents=True)
        outside = self.directory / "outside"
        (outside / "Cache").mkdir(parents=True)
        marker = outside / "Cache/personal.txt"
        marker.write_bytes(b"kept outside profile")
        self.junction(base / "Default", outside)
        real_cache = self.directory_at("local", "Google/Chrome/User Data/Profile 2/Cache")
        chrome = self.categories()["chrome_cache"]
        self.assertEqual(chrome.roots, (real_cache,))
        self.assertEqual(marker.read_bytes(), b"kept outside profile")

        edge_outside = self.directory / "outside-edge"
        (edge_outside / "Edge/User Data/Default/Cache").mkdir(parents=True)
        self.junction(self.locations["local"] / "Microsoft", edge_outside)
        self.assertFalse(self.categories()["edge_cache"].available)

    def test_firefox_and_opera_only_include_browser_cache_folders(self):
        firefox = self.directory_at("local", "Mozilla/Firefox/Profiles/abcd.default-release/cache2/entries")
        personal = self.file_at("roaming", "Mozilla/Firefox/Profiles/abcd.default-release/cookies.sqlite")
        opera_expected = set()
        for location, name, profile in (("local", "Opera Stable", ""),
                                        ("roaming", "Opera GX Stable", "Default")):
            for leaf in ("Cache", "Code Cache", "GPUCache"):
                relative = Path("Opera Software") / name / profile / leaf
                opera_expected.add(self.directory_at(location, relative))
        categories = self.categories()
        self.assertEqual(categories["firefox_cache"].roots, (firefox,))
        self.assertEqual(set(categories["opera_cache"].roots), opera_expected)
        self.assertIn("firefox.exe", categories["firefox_cache"].processes)
        self.assertIn("opera.exe", categories["opera_cache"].processes)
        self.assert_not_targeted(categories, [personal])

    def test_application_cache_leaves_preserve_account_settings_and_projects(self):
        discord_roots = {self.directory_at("roaming", f"discord/{leaf}")
                         for leaf in ("Code Cache", "GPUCache")}
        editor_leaves = ("Cache", "CachedData", "Code Cache", "GPUCache", "CachedExtensionVSIXs")
        vscode_roots = {self.directory_at("roaming", f"Code/{leaf}") for leaf in editor_leaves}
        cursor_roots = {self.directory_at("roaming", f"Cursor/{leaf}") for leaf in editor_leaves}
        personal = [
            self.file_at("roaming", "discord/Cache/favorited-image"),
            self.file_at("roaming", "discord/Local Storage/leveldb/account.log"),
            self.file_at("roaming", "Code/User/settings.json"),
            self.file_at("roaming", "Code/extensions/installed-extension/package.json"),
            self.file_at("roaming", "Cursor/User/workspaceStorage/project/state.vscdb"),
            self.file_at("roaming", "Spotify/Storage/offline-track"),
            self.file_at("local", "Spotify/Storage/offline-track"),
        ]
        categories = self.categories()
        self.assertEqual(set(categories["discord_cache"].roots), discord_roots)
        self.assertEqual(set(categories["vscode_cache"].roots), vscode_roots)
        self.assertEqual(set(categories["cursor_cache"].roots), cursor_roots)
        self.assert_not_targeted(categories, personal)

    def test_epic_supports_legacy_and_default_profile_cache_without_account_data(self):
        expected = set()
        for version, profile in (("webcache", ""), ("webcache_4430", "Default")):
            for leaf in ("Cache", "Code Cache", "GPUCache"):
                expected.add(self.directory_at("local", Path("EpicGamesLauncher/Saved") / version / profile / leaf))
        self.directory_at("local", "EpicGamesLauncher/Saved/webcache_backup/Cache")
        personal = [self.file_at("local", "EpicGamesLauncher/Saved/webcache_4430/Default/Cookies"),
                    self.file_at("local", "EpicGamesLauncher/Saved/webcache_4430/Default/Local Storage/account")]
        categories = self.categories()
        self.assertEqual(set(categories["epic_cache"].roots), expected)
        self.assertIn("EpicWebHelper.exe", categories["epic_cache"].processes)
        self.assert_not_targeted(categories, personal)

    def test_developer_caches_do_not_include_installed_packages_or_projects(self):
        pip_roots = {self.directory_at("local", f"pip/Cache/{leaf}")
                     for leaf in ("http", "http-v2", "wheels")}
        npm = self.directory_at("local", "npm-cache/_cacache")
        nuget = self.directory_at("local", "NuGet/v3-cache")
        personal = [
            self.file_at("local", "Programs/Python/Python312/Lib/site-packages/package/__init__.py"),
            self.file_at("roaming", "Python/Python312/site-packages/package/__init__.py"),
            self.file_at("local", "npm/node_modules/tool/package.json"),
            self.file_at("local", "NuGet/packages/library/lib.dll"),
        ]
        categories = self.categories()
        self.assertEqual(set(categories["pip_cache"].roots), pip_roots)
        self.assertEqual(categories["npm_cache"].roots, (npm,))
        self.assertEqual(categories["nuget_cache"].roots, (nuget,))
        self.assertEqual(categories["pip_cache"].group, "Developer tools")
        self.assert_not_targeted(categories, personal)

    def test_shader_cache_roots_are_specific_and_cover_local_low(self):
        directx = self.directory_at("local", "D3DSCache")
        nvidia_local = self.directory_at("local", "NVIDIA/DXCache")
        nvidia_low = self.directory_at("low", "NVIDIA/PerDriverVersion/DXCache")
        amd_local = self.directory_at("local", "AMD/VkCache")
        amd_low = self.directory_at("low", "AMD/DxCache")
        personal = [self.file_at("local", "NVIDIA/settings/user.cfg"),
                    self.file_at("local", "AMD/recordings/video.mp4")]
        categories = self.categories()
        self.assertEqual(categories["directx_cache"].roots, (directx,))
        self.assertEqual(set(categories["nvidia_cache"].roots), {nvidia_local, nvidia_low})
        self.assertEqual(set(categories["amd_cache"].roots), {amd_local, amd_low})
        self.assert_not_targeted(categories, personal)

    def test_error_reports_use_specific_leaves_and_essential_system_folders_are_not_targets(self):
        expected = {self.directory_at(location, Path("Microsoft/Windows/WER") / leaf)
                    for location in ("local", "common")
                    for leaf in ("ReportArchive", "ReportQueue", "Temp")}
        protected = [
            self.file_at("local", "Microsoft/Windows/WER/LocalDumps/custom.txt"),
            self.file_at("common", "Microsoft/Windows/WER/account-settings.json"),
            self.file_at("windows", "WinSxS/component/system.dll"),
            self.file_at("windows", "Installer/package.msi"),
        ]
        categories = self.categories()
        self.assertEqual(set(categories["error_reports"].roots), expected)
        self.assert_not_targeted(categories, protected)

    def test_windows_rules_remove_recent_matching_files_and_keep_other_data(self):
        removed = [
            self.file_at("local", "CrashDumps/recent.DMP"),
            self.file_at("windows", "Logs/CBS/CBS.log"),
            self.file_at("windows", "Logs/CBS/CbsPersist_20261022.cab"),
            self.file_at("windows", "Logs/DISM/dism.log"),
            self.file_at("windows", "Logs/DISM/dism.log.bak"),
            self.file_at("local", "Microsoft/Windows/Explorer/THUMBCACHE_256.DB"),
        ]
        kept = [
            self.file_at("local", "CrashDumps/personal.txt"),
            self.file_at("local", "CrashDumps/my-project/readme.md"),
            self.file_at("windows", "Logs/CBS/personal.cab"),
            self.file_at("windows", "Logs/CBS/subfolder/CBS.log"),
            self.file_at("windows", "Logs/DISM/other.log"),
            self.file_at("local", "Microsoft/Windows/Explorer/iconcache_256.db"),
            self.file_at("local", "Microsoft/Windows/Explorer/personal.db"),
            self.file_at("local", "Microsoft/Windows/Explorer/nested/thumbcache_256.db"),
        ]
        categories = self.categories()
        selected = ("crash_dumps", "windows_cbs_logs", "windows_dism_logs", "thumbnail_cache")
        self.assertFalse(categories["thumbnail_cache"].recursive)
        self.assertTrue(categories["crash_dumps"].file_patterns)
        self.assertTrue(categories["windows_cbs_logs"].file_patterns)
        self.assertTrue(categories["windows_dism_logs"].file_patterns)
        cleaner = Cleaner(categories.values(), process_provider=lambda: set())
        plan = cleaner.scan(selected)
        self.assertEqual({entry.path for entry in plan.entries}, set(removed))
        self.assertTrue(all(path.exists() for path in removed + kept), "Analysis must not modify files")
        result = cleaner.clean(plan, selected)
        self.assertEqual(result.deleted_files, len(removed))
        self.assertTrue(all(not path.exists() for path in removed))
        self.assertTrue(all(path.exists() for path in kept))
        self.assertTrue((self.locations["local"] / "CrashDumps/my-project").is_dir())


if __name__ == "__main__":
    unittest.main()
