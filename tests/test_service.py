from pathlib import Path
import json
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
import unittest

from clearspace.gui import AssetServer, DesktopApi, _allowed_navigation
from clearspace.service import CleanerService, ServiceError


class ControlledCleaner:
    def __init__(self, *, blocked=False, truncated=False):
        self.categories = (
            SimpleNamespace(id="user_temp", label="Temporary files", description="Temporary files", available=True, reason=None, default_selected=True),
            SimpleNamespace(id="pip_cache", label="pip cache", description="HTTP cache", available=True, reason=None, default_selected=False),
        )
        self.started = threading.Event()
        self.release = threading.Event()
        if not blocked:
            self.release.set()
        self.truncated = truncated
        self.clean_calls = []
        self.excluded_calls = []

    def scan(self, ids, cancel, progress):
        self.started.set()
        while not self.release.wait(0.01) and not cancel.is_set():
            pass
        progress({"phase": "scanning", "visited_files": 2, "found_files": 1, "found_bytes": 123, "category_id": ids[0]})
        return SimpleNamespace(
            id="scan-token", created_at=time.time(), expires_at=time.time() + 1800, cancelled=cancel.is_set(), truncated=self.truncated,
            entries=tuple(SimpleNamespace(id=f"entry-{index}", category_id=ids[0], path=Path(f"example-{index}.tmp"), size=123) for index in range(105)),
            category_stats=tuple(SimpleNamespace(category_id=category_id, files=105, bytes=12915, skipped=0, reason=None) for category_id in ids),
        )

    def clean(self, plan, ids, cancel, progress, excluded_entry_ids=()):
        self.clean_calls.append((plan.id, ids))
        self.excluded_calls.append(tuple(excluded_entry_ids))
        return SimpleNamespace(deleted_files=2, freed_bytes=246, skipped_files=1, failed_files=0, cancelled=False,
                               issues=({"path": "locked.tmp", "reason": "File is in use, unavailable or no longer accessible; kept."},))


def wait_until(service, phase):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if service.get_state()["phase"] == phase:
            return service.get_state()
        time.sleep(0.005)
    raise AssertionError(f"Expected phase {phase}: {service.get_state()}")


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="clearspace-service-test-")
        self.data_dir = Path(self.temporary.name)
        self.services = []

    def tearDown(self):
        for service in self.services:
            service.shutdown()
        self.temporary.cleanup()

    def service(self, cleaner=None):
        service = CleanerService(cleaner=cleaner or ControlledCleaner(), data_dir=self.data_dir)
        self.services.append(service)
        return service

    def test_scan_never_cleans_and_forged_scan_cannot_authorize_cleanup(self):
        engine = ControlledCleaner()
        service = self.service(engine)
        with self.assertRaises(ServiceError):
            service.clean("scan-token", ["user_temp"])
        service.scan()
        state = wait_until(service, "ready")
        self.assertEqual(engine.clean_calls, [])
        with self.assertRaises(ServiceError):
            service.clean("forged", ["user_temp"])
        service.clean(state["scan_id"], ["user_temp"])
        result = wait_until(service, "completed")
        self.assertEqual(result["result"]["removed"], 2)
        self.assertEqual(engine.clean_calls, [("scan-token", ["user_temp"])])
        with self.assertRaises(ServiceError):
            service.clean("scan-token", ["user_temp"])

    def test_changing_selection_invalidates_old_preview_and_persists_ids_only(self):
        service = self.service()
        service.scan()
        wait_until(service, "ready")
        state = service.set_selection(["pip_cache"])
        self.assertIsNone(state["scan_id"])
        self.assertEqual(state["phase"], "idle")
        with self.assertRaises(ServiceError):
            service.clean("scan-token", ["pip_cache"])
        stored = json.loads((self.data_dir / "settings.json").read_text())
        self.assertEqual(stored, {"version": 3, "selected": ["pip_cache"], "known": sorted(row["id"] for row in state["categories"])})
        restored = self.service().get_state()
        self.assertEqual([row["id"] for row in restored["categories"] if row["selected"]], ["pip_cache"])

    def test_new_recommended_categories_start_selected_once(self):
        service = self.service()
        known = [row["id"] for row in service.get_state()["categories"] if row["id"] != "user_temp"]
        (self.data_dir / "settings.json").write_text(json.dumps({"version": 3, "selected": ["pip_cache"], "known": known}))
        selected = {row["id"] for row in self.service().get_state()["categories"] if row["selected"]}
        self.assertEqual(selected, {"pip_cache", "user_temp"})
        # A deselected category the user had already seen stays deselected.
        (self.data_dir / "settings.json").write_text(json.dumps({"version": 3, "selected": ["pip_cache"], "known": known + ["user_temp"]}))
        self.assertEqual({row["id"] for row in self.service().get_state()["categories"] if row["selected"]}, {"pip_cache"})

    def test_deselecting_and_reselecting_scanned_categories_preserves_plan(self):
        engine = ControlledCleaner()
        service = self.service(engine)
        service.set_selection(["user_temp", "pip_cache"])
        service.scan()
        state = wait_until(service, "ready")
        plan = service._plan
        self.assertEqual(service.set_selection(["user_temp"])["scan_id"], state["scan_id"])
        self.assertEqual(service.set_selection([])["scan_id"], state["scan_id"])
        self.assertEqual(service.set_selection(["pip_cache"])["scan_id"], state["scan_id"])
        self.assertIs(service._plan, plan)
        service.clean(state["scan_id"], ["pip_cache"])
        wait_until(service, "completed")
        self.assertEqual(engine.clean_calls, [("scan-token", ["pip_cache"])])
        self.assertIsNone(service.set_selection(["user_temp"])["scan_id"])

    def test_expired_plan_cannot_be_reselected_previewed_or_cleaned(self):
        service = self.service()
        service.scan()
        wait_until(service, "ready")
        service._plan.expires_at = time.time() - 1
        state = service.get_state()
        self.assertIsNone(state["scan_id"])
        self.assertEqual(state["phase"], "idle")
        self.assertIn("expired", state["error"])
        with self.assertRaises(ServiceError):
            service.get_files("scan-token", "user_temp")
        with self.assertRaises(ServiceError):
            service.clean("scan-token", ["user_temp"])
        self.assertIsNone(service.set_selection([])["scan_id"])

    def test_exclusions_use_only_snapshot_ids_and_update_totals(self):
        engine = ControlledCleaner()
        service = self.service(engine)
        service.scan()
        state = wait_until(service, "ready")
        item = service.get_files(state["scan_id"], "user_temp")["items"][0]
        for scan, entry, included in (("wrong", item["id"], False), (state["scan_id"], "../../Documents", False),
                                     (state["scan_id"], item["path"], False), (state["scan_id"], None, False),
                                     (state["scan_id"], item["id"], "false"), (state["scan_id"], [item["id"]], False)):
            with self.assertRaises(ServiceError):
                service.set_file_included(scan, entry, included)
        state = service.set_file_included(state["scan_id"], item["id"], False)
        category = next(row for row in state["categories"] if row["id"] == "user_temp")
        self.assertEqual((category["files"], category["bytes"], category["excluded_files"]), (104, 12792, 1))
        self.assertFalse(service.get_files(state["scan_id"], "user_temp")["items"][0]["included"])
        # Changing the category selection retains the per-file choice for this plan.
        service.set_selection([])
        service.set_selection(["user_temp"])
        service.clean(state["scan_id"], ["user_temp"])
        result = wait_until(service, "completed")["result"]
        self.assertEqual(engine.excluded_calls, [(item["id"],)])
        self.assertEqual(result["excluded"], 1)
        self.assertEqual(result["issues"][0]["path"], "locked.tmp")
        with self.assertRaises(ServiceError):
            service.set_file_included("scan-token", item["id"], True)

    def test_file_exclusions_can_be_restored_and_do_not_apply_to_native_actions(self):
        engine = ControlledCleaner()
        service = self.service(engine)
        service.scan()
        state = wait_until(service, "ready")
        service.set_file_included(state["scan_id"], "entry-0", False)
        restored = service.set_file_included(state["scan_id"], "entry-0", True)
        self.assertEqual(restored["categories"][0]["files"], 105)
        self.assertEqual(restored["categories"][0]["excluded_files"], 0)
        # A malformed native preview must never grant file-level authority.
        engine.categories[0].native_action = "empty_recycle_bin"
        with self.assertRaises(ServiceError):
            service.set_file_included(state["scan_id"], "entry-0", False)
        with self.assertRaises(ServiceError):
            service.get_files(state["scan_id"], "user_temp")

    def test_cancelled_or_truncated_scan_cannot_clean(self):
        for truncated in (False, True):
            engine = ControlledCleaner(blocked=not truncated, truncated=truncated)
            service = self.service(engine)
            service.scan()
            self.assertTrue(engine.started.wait(1))
            if not truncated:
                service.cancel()
            state = wait_until(service, "cancelled")
            self.assertIsNone(state["scan_id"])
            with self.assertRaises(ServiceError):
                service.clean("scan-token", ["user_temp"])
            self.assertEqual(engine.clean_calls, [])

    def test_only_one_operation_and_no_selection_change_while_busy(self):
        engine = ControlledCleaner(blocked=True)
        service = self.service(engine)
        service.scan()
        for operation in (service.scan, lambda: service.set_selection([]), lambda: service.clean("scan-token", ["user_temp"])):
            with self.assertRaises(ServiceError):
                operation()
        service.cancel()
        wait_until(service, "cancelled")

    def test_details_are_bound_to_current_scan_and_paginated(self):
        service = self.service()
        service.scan()
        wait_until(service, "ready")
        first = service.get_files("scan-token", "user_temp", 0)
        second = service.get_files("scan-token", "user_temp", 100)
        self.assertEqual((len(first["items"]), first["total"], first["next_offset"]), (100, 105, 100))
        self.assertEqual((len(second["items"]), second["next_offset"]), (5, None))
        for scan_id, offset in (("wrong", 0), ("scan-token", -1), ("scan-token", True), ("scan-token", 1000)):
            with self.assertRaises(ServiceError):
                service.get_files(scan_id, "user_temp", offset)

    def test_malformed_bridge_requests_do_not_reach_cleanup(self):
        engine = ControlledCleaner()
        api = DesktopApi(self.service(engine))
        for ids in ("user_temp", ["../../Documents"], ["user_temp", "user_temp"], [1], None):
            self.assertFalse(api.set_selection(ids)["ok"])
        self.assertFalse(api.clean(None, ["user_temp"])["ok"])
        self.assertEqual(engine.clean_calls, [])

    def test_bridge_reports_update_state_and_refuses_updates_without_an_updater(self):
        updater = SimpleNamespace(snapshot=lambda: {"status": "available", "version": "9.0.0", "progress": None, "error": None})
        self.assertEqual(DesktopApi(self.service(), updater).get_state()["state"]["update"]["version"], "9.0.0")
        api = DesktopApi(self.service())
        self.assertNotIn("update", api.get_state()["state"])
        for method in (api.download_update, api.install_update, api.dismiss_update, api.open_update_page):
            self.assertEqual(method(), {"ok": False, "error": "Updates are not available in this copy."})

    def test_window_actions_are_validated_before_reaching_windows(self):
        api = DesktopApi(self.service())
        self.assertEqual(api.window_action("close"), {"ok": False, "error": "The window is not ready yet."})
        api.attach(object())
        for action in ("shutdown", "../close", None, ["drag"]):
            self.assertFalse(api.window_action(action)["ok"])

    def test_returned_state_is_a_copy(self):
        service = self.service()
        state = service.get_state()
        state["categories"][0]["selected"] = False
        self.assertTrue(service.get_state()["categories"][0]["selected"])

    def test_theme_is_validated_persisted_and_allowed_while_busy(self):
        service = self.service()
        self.assertEqual(service.get_state()["theme"], "system")
        for theme in ("blue", None, ["dark"]):
            with self.assertRaises(ServiceError):
                service.set_theme(theme)
        self.assertFalse(DesktopApi(service).set_theme("../dark")["ok"])
        self.assertEqual(service.set_theme("light")["theme"], "light")
        stored = json.loads((self.data_dir / "appearance.json").read_text())
        self.assertEqual(stored, {"version": 1, "theme": "light", "language": "system"})
        self.assertEqual(self.service().get_state()["theme"], "light")
        for language in ("fr", None, "../pt"):
            with self.assertRaises(ServiceError):
                service.set_language(language)
        self.assertEqual(service.set_language("pt")["language"], "pt")
        self.assertEqual(self.service().get_state()["theme"], "light")
        self.assertEqual(self.service().get_state()["language"], "pt")
        (self.data_dir / "appearance.json").write_text('{"theme": "neon"}')
        self.assertEqual(self.service().get_state()["theme"], "system")
        engine = ControlledCleaner(blocked=True)
        busy = self.service(engine)
        busy.scan()
        self.assertEqual(busy.set_theme("dark")["phase"], "scanning")
        busy.cancel()
        wait_until(busy, "cancelled")

    def test_mvp_preferences_gain_new_windows_recommendations_once(self):
        (self.data_dir / "settings.json").write_text(json.dumps({"version": 1, "selected": ["pip_cache"]}))
        cleaner = ControlledCleaner()
        cleaner.categories += (SimpleNamespace(id="windows_temp", label="Windows temporary files", description="System temporary files", available=True, reason=None, default_selected=True),)
        service = self.service(cleaner)
        selected = {row["id"] for row in service.get_state()["categories"] if row["selected"]}
        self.assertEqual(selected, {"pip_cache", "windows_temp"})
        service.set_selection([])
        restored = self.service(cleaner)
        self.assertFalse(any(row["selected"] for row in restored.get_state()["categories"]))


class LocalRendererTests(unittest.TestCase):
    def test_navigation_allows_only_current_document_and_fragment(self):
        url = "http://127.0.0.1:12345/token/index.html"
        self.assertTrue(_allowed_navigation(url + "#details", url))
        for other in ("https://example.com", "file:///C:/Windows/win.ini", url + "?x=1", "http://localhost:12345/token/index.html"):
            self.assertFalse(_allowed_navigation(other, url))

    def test_asset_server_rejects_outside_paths_and_wrong_host(self):
        import http.client
        from urllib.parse import urlsplit
        with TemporaryDirectory(prefix="clearspace-server-test-") as temporary:
            assets = Path(temporary) / "assets"
            assets.mkdir()
            (assets / "index.html").write_text("<html>Clearspace</html>")
            (Path(temporary) / "secret.txt").write_text("outside")
            server = AssetServer(assets)
            try:
                url = urlsplit(server.url)
                connection = http.client.HTTPConnection(url.hostname, url.port, timeout=2)
                connection.request("GET", url.path)
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn("connect-src 'none'", response.getheader("Content-Security-Policy"))
                response.read()
                connection.request("GET", url.path, headers={"Host": "example.com"})
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                connection.request("GET", url.path.rsplit("/", 1)[0] + "/%2e%2e/secret.txt")
                response = connection.getresponse()
                self.assertEqual(response.status, 404)
                response.read()
                connection.close()
            finally:
                server.close()


if __name__ == "__main__":
    unittest.main()
