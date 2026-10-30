"""Windows Disk Cleanup handlers: selection rules and engine integration without purging."""

from __future__ import annotations

import os
import threading
import unittest
from unittest.mock import patch

from clearspace import actions, volume_caches
from clearspace.engine import Category, Cleaner
from clearspace.maintenance import ActionResult, ActionSnapshot

HANDLER = volume_caches.Handler("Internet Cache Files", "{9B0EFD60-F7B0-11D0-BAEF-00C04FC308C9}", "Temporary Internet Files", "")


class VolumeCacheTests(unittest.TestCase):
    def test_category_ids_are_plain_identifiers(self):
        self.assertEqual(volume_caches.category_id("Internet Cache Files"), "disk_cleanup_internet_cache_files")
        self.assertEqual(volume_caches.category_id("Windows ESD installation files"), "disk_cleanup_windows_esd_installation_files")

    @unittest.skipUnless(os.name == "nt", "reads the Windows registry")
    def test_personal_and_duplicate_handlers_are_never_offered(self):
        keys = {handler.key.casefold() for handler in volume_caches.handlers()}
        for excluded in ("DownloadsFolder", "User file versions", "Recycle Bin", "Temporary Files", "Update Cleanup"):
            self.assertNotIn(excluded.casefold(), keys)

    def test_consequential_handlers_are_not_preselected(self):
        handlers = {volume_caches.category_id(key): volume_caches.Handler(key, "{00000000-0000-0000-0000-000000000001}", key, "")
                    for key in ("Previous Installations", "Windows ESD installation files", "Device Driver Packages", "Internet Cache Files", "Unknown Vendor Cache")}
        with patch.object(volume_caches, "by_category", return_value=handlers):
            categories = {category.id: category for category in actions.disk_cleanup_categories()}
        self.assertTrue(categories["disk_cleanup_internet_cache_files"].default_selected)
        for key in ("previous_installations", "windows_esd_installation_files", "device_driver_packages", "unknown_vendor_cache"):
            self.assertFalse(categories["disk_cleanup_" + key].default_selected, key)
        self.assertEqual(categories["disk_cleanup_unknown_vendor_cache"].label, "Unknown Vendor Cache")
        self.assertEqual({category.group for category in categories.values()}, {"Disk Cleanup"})

    def test_provider_reports_size_and_administrator_only_handlers(self):
        with patch.object(volume_caches, "measure", return_value=23068672):
            self.assertEqual(volume_caches.Provider(HANDLER, administrator=lambda: False).query(), ActionSnapshot(True, 23068672))
        with patch.object(volume_caches, "measure", return_value=0):
            self.assertEqual(volume_caches.Provider(HANDLER, administrator=lambda: False).query(), ActionSnapshot(False, 0))
        with patch.object(volume_caches, "measure", return_value=None):
            snapshot = volume_caches.Provider(HANDLER, administrator=lambda: False).query()
            self.assertFalse(snapshot.pending)
            self.assertIn("administrator", snapshot.detail)
            self.assertIsNone(volume_caches.Provider(HANDLER, administrator=lambda: True).query().detail)

    def test_stop_before_cleanup_never_calls_windows(self):
        cancel = threading.Event()
        cancel.set()
        with patch.object(volume_caches, "purge") as purge:
            result = volume_caches.Provider(HANDLER, administrator=lambda: True).clean(ActionSnapshot(True, 1), cancel=cancel)
        purge.assert_not_called()
        self.assertFalse(result.completed)

    def test_engine_analyzes_then_cleans_through_the_provider(self):
        identifier = volume_caches.category_id(HANDLER.key)

        class Fake:
            def __init__(self):
                self.cleaned = []

            def query(self, cancel=None):
                return ActionSnapshot(True, 2048)

            def clean(self, snapshot, cancel=None):
                self.cleaned.append(snapshot)
                return ActionResult(True, 2048)

        fake = Fake()
        category = Category(identifier, "Temporary Internet files", "", (), group="Disk Cleanup", native_action=identifier)
        with patch.object(volume_caches, "by_category", return_value={identifier: HANDLER}):
            cleaner = Cleaner([category], process_provider=lambda: set(), native_actions={identifier: fake})
            plan = cleaner.scan([identifier])
            self.assertEqual(plan.category_stats[0].bytes, 2048)
            self.assertEqual(plan.category_stats[0].actions, 1)
            result = cleaner.clean(plan, [identifier])
        self.assertEqual(len(fake.cleaned), 1)
        self.assertEqual(result.freed_bytes, 2048)
        self.assertEqual(result.action_results[0]["status"], "completed")

    def test_unregistered_identifiers_are_rejected(self):
        with patch.object(volume_caches, "by_category", return_value={}):
            self.assertFalse(actions.is_native_action("disk_cleanup_downloadsfolder"))
            with self.assertRaises(ValueError):
                actions.create_provider("disk_cleanup_downloadsfolder")


if __name__ == "__main__":
    unittest.main()
