"""JSON-lines bridge for headless UI verification, exclusively in its own sandbox."""

from pathlib import Path
import json
import sys
import tempfile

from clearspace.diagnostics import _backdate_fixture
from clearspace.engine import Category, Cleaner
from clearspace.gui import AssetServer, DesktopApi
from clearspace.service import CleanerService


def main():
    with tempfile.TemporaryDirectory(prefix="clearspace-ui-check-") as temporary:
        root = Path(temporary)
        categories = []
        for identifier, title, description, selected in (
            ("user_temp", "Temporary files", "Temporary files older than 7 days.", True),
            ("chrome_cache", "Google Chrome", "Disk cache only. Close Chrome before cleaning.", False),
            ("edge_cache", "Microsoft Edge", "Disk cache only. Close Edge before cleaning.", False),
            ("firefox_cache", "Mozilla Firefox", "Disk cache only. Close Firefox before cleaning.", False),
            ("pip_cache", "Python package cache", "Old download cache. Packages may need downloading again.", False),
        ):
            folder = root / identifier
            folder.mkdir()
            categories.append(Category(identifier, title, description, (folder,), default_selected=selected))
        samples = []
        for identifier in ("user_temp", "chrome_cache"):
            path = root / identifier / "old-cache.tmp"
            path.write_bytes(b"clearspace-owned-ui-test" * 512)
            _backdate_fixture(path)
            samples.append(path)
        recent = root / "user_temp/recent.tmp"
        recent.write_bytes(b"preserve recent")
        personal = root / "personal.txt"
        personal.write_bytes(b"preserve outside scope")
        service = CleanerService(Cleaner(categories=categories), root / "settings")
        api = DesktopApi(service)
        server = AssetServer()
        allowed = {name: getattr(api, name) for name in ("get_state", "set_selection", "scan", "cancel", "clean", "get_files")}
        try:
            for line in sys.stdin:
                request = json.loads(line)
                method = request["method"]
                if method == "asset_url":
                    result = server.url
                elif method == "fixture_status":
                    result = {"old_present": [path.exists() for path in samples], "recent_kept": recent.read_bytes() == b"preserve recent", "personal_kept": personal.read_bytes() == b"preserve outside scope"}
                elif method in allowed:
                    result = allowed[method](*request.get("args", []))
                else:
                    raise ValueError("Unsupported verification request")
                print(json.dumps({"id": request["id"], "value": result}, ensure_ascii=True), flush=True)
        finally:
            service.shutdown()
            server.close()


if __name__ == "__main__":
    main()
