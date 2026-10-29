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

    def scan(self, ids, cancel, progress):
        self.started.set()
        while not self.release.wait(0.01) and not cancel.is_set():
            pass
        progress({"phase": "scanning", "visited_files": 2, "found_files": 1, "found_bytes": 123, "category_id": ids[0]})
        return SimpleNamespace(
            id="scan-token", created_at=time.time(), cancelled=cancel.is_set(), truncated=self.truncated,
            entries=tuple(SimpleNamespace(category_id=ids[0], path=Path(f"example-{index}.tmp"), size=123) for index in range(105)),
            category_stats=tuple(SimpleNamespace(category_id=category_id, files=105, bytes=12915, skipped=0, reason=None) for category_id in ids),
        )

    def clean(self, plan, ids, cancel, progress):
        self.clean_calls.append((plan.id, ids))
        return SimpleNamespace(deleted_files=2, freed_bytes=246, skipped_files=1, failed_files=0, cancelled=False)


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
        self.assertEqual(stored, {"version": 2, "selected": ["pip_cache"]})
        restored = self.service().get_state()
        self.assertEqual([row["id"] for row in restored["categories"] if row["selected"]], ["pip_cache"])

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
        self.assertEqual(stored, {"version": 1, "theme": "light"})
        self.assertEqual(self.service().get_state()["theme"], "light")
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
