"""Release smoke test: only its own newly created sandbox can be modified."""

from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
import time


def verify_portable():
    from .engine import Category, Cleaner
    from .gui import ASSETS, DesktopApi
    from .service import CleanerService
    import webview  # noqa: F401 - verify the packaged native host can import

    if os.name != "nt" or not (ASSETS / "index.html").is_file():
        raise RuntimeError("Portable runtime or renderer is missing.")
    with tempfile.TemporaryDirectory(prefix="clearspace-portable-check-") as temporary, ExitStack() as held:
        root = Path(temporary)
        cache = root / "supported-cache"
        cache.mkdir()
        old = cache / "old.tmp"
        old.write_bytes(b"clearspace-owned-test-data")
        recent = cache / "recent.tmp"
        recent.write_bytes(b"keep recent file")
        # Recent files are eligible; this one is kept because it is in use.
        held.enter_context(recent.open("rb"))
        personal = root / "personal.txt"
        personal.write_bytes(b"keep outside category")
        cleaner = Cleaner(categories=[Category("user_temp", "Temporary files", "Test fixture only", (cache,), default_selected=True)])
        service = CleanerService(cleaner, root / "state")
        api = DesktopApi(service)
        try:
            assert api.scan()["ok"]
            deadline = time.monotonic() + 10
            while service.active() and time.monotonic() < deadline:
                time.sleep(0.01)
            ready = api.get_state()["state"]
            assert ready["phase"] == "ready" and ready["categories"][0]["files"] == 1
            assert old.exists() and recent.exists() and personal.exists()
            assert not api.clean("wrong-token", ["user_temp"])["ok"]
            details = api.get_files(ready["scan_id"], "user_temp", 0)
            assert details["ok"] and details["items"][0]["path"] == str(old)
            assert api.clean(ready["scan_id"], ["user_temp"])["ok"]
            deadline = time.monotonic() + 10
            while service.active() and time.monotonic() < deadline:
                time.sleep(0.01)
            result = api.get_state()["state"]
            assert result["phase"] == "completed" and result["result"]["removed"] == 1
            assert not old.exists() and recent.read_bytes() == b"keep recent file"
            assert personal.read_bytes() == b"keep outside category"
        finally:
            service.shutdown()
