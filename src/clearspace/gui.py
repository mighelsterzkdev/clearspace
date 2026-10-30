"""Local desktop host with a read-only asset server and a narrow native bridge."""

from __future__ import annotations

from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import mimetypes
import multiprocessing
import os
from pathlib import Path
import secrets
import sys
import threading
from urllib.parse import unquote, urlsplit

from . import APP_NAME, __version__, frame, shell, updates
from .elevation import ElevationError, relaunch_as_administrator
from .maintenance import is_administrator
from .service import CleanerService, ServiceError
from .windows import local_app_data


ASSETS = Path(__file__).resolve().parent / "desktop_assets"
CSP = (
    "default-src 'none'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'none'; frame-src 'none'; "
    "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


class DesktopApi:
    def __init__(self, service, updater=None):
        self._service = service
        self._updater = updater
        self._window = None

    def attach(self, window):
        self._window = window

    def _call(self, method, *args, details=False):
        try:
            result = method(*args)
            if details:
                return {"ok": True, **result}
            if isinstance(result, dict) and self._updater is not None:
                result = {**result, "update": self._updater.snapshot()}
            return {"ok": True, "state": result}
        except Exception as error:
            text = str(error) if isinstance(error, (ServiceError, updates.UpdateError)) else "The request could not finish. Analyze again to retry."
            return {"ok": False, "error": " ".join(text.split())[:500]}

    def get_state(self):
        return self._call(self._service.get_state)

    def set_selection(self, ids):
        return self._call(self._service.set_selection, ids)

    def scan(self):
        return self._call(self._service.scan)

    def cancel(self):
        return self._call(self._service.cancel)

    def clean(self, scan_id, ids):
        return self._call(self._service.clean, scan_id, ids)

    def get_files(self, scan_id, category_id, offset=0):
        return self._call(self._service.get_files, scan_id, category_id, offset, details=True)

    def open_search_settings(self):
        return self._call(self._service.open_search_settings)

    def window_action(self, action):
        try:
            if self._window is None:
                raise ServiceError("The window is not ready yet.")
            frame.window_action(self._window, action)
            return {"ok": True}
        except (ServiceError, ValueError) as error:
            return {"ok": False, "error": str(error)}
        except Exception:
            return {"ok": False, "error": "The window could not respond."}

    def set_theme(self, theme):
        response = self._call(self._service.set_theme, theme)
        if response["ok"] and self._window is not None:
            _apply_titlebar(self._window, _uses_dark_titlebar(theme))
        return response

    def set_language(self, language):
        return self._call(self._service.set_language, language)

    def restart_as_administrator(self):
        def restart():
            if self._service.active():
                raise ServiceError("Wait for the current operation or stop it first.")
            try:
                launched = relaunch_as_administrator()
            except ElevationError as error:
                raise ServiceError(str(error)) from error
            if launched and self._window is not None:
                # The elevated copy owns the session now; cancelling UAC keeps this one.
                self._window.destroy()
            return self._service.get_state()
        return self._call(restart)

    def _update_call(self, action):
        def run():
            if self._updater is None:
                raise ServiceError("Updates are not available in this copy.")
            action(self._updater)
            return self._service.get_state()
        return self._call(run)

    def download_update(self):
        return self._update_call(lambda updater: updater.download())

    def dismiss_update(self):
        return self._update_call(lambda updater: updater.dismiss())

    def open_update_page(self):
        return self._update_call(lambda updater: os.startfile(updater.release_page()))

    def install_update(self):
        def install(updater):
            if self._service.active():
                raise ServiceError("Wait for the current operation or stop it first.")
            updater.install()
            if self._window is not None:
                # The staged copy waits for this process to exit, installs, then reopens Clearspace.
                self._window.destroy()
        return self._update_call(install)


_NOTICES = {
    "pt": {"analyzed": ("Análise concluída", "{size} podem ser liberados."), "cleaned": ("Limpeza concluída", "{size} liberados."),
           "failed": ("Limpeza concluída com erros", "{size} liberados. Abra o Clearspace para ver os detalhes.")},
    "en": {"analyzed": ("Analysis complete", "{size} can be freed."), "cleaned": ("Cleanup complete", "{size} freed."),
           "failed": ("Cleanup finished with errors", "{size} freed. Open Clearspace for details.")},
}


def _size(value):
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024


def _watch(service, feedback, stop):
    """Mirror analysis and cleanup on the taskbar and report when they finish."""
    previous = None
    while not stop.wait(0.5):
        try:
            state = service.get_state()
        except Exception:
            continue
        phase = state["phase"]
        feedback.progress(shell.INDETERMINATE if phase in ("scanning", "cleaning") else shell.NO_PROGRESS)
        if previous in ("scanning", "cleaning") and phase in ("ready", "completed"):
            notices = _NOTICES[shell.language(service.language())]
            if phase == "ready":
                size = sum(row["bytes"] for row in state["categories"] if row["selected"] and row["available"])
                key = "analyzed"
            else:
                result = state.get("result") or {}
                size = result.get("freed_bytes", 0)
                key = "failed" if result.get("failed") or result.get("actions_failed") else "cleaned"
            title, message = notices[key]
            feedback.notify(title, message.format(size=_size(size)))
        previous = phase


class _AssetHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_HEAD(self):
        self._serve(False)

    def do_GET(self):
        self._serve(True)

    def _serve(self, body):
        if self.headers.get("Host") != self.server.expected_host:
            self.send_error(403)
            return
        parts = urlsplit(self.path)
        if parts.scheme or parts.netloc or parts.query or not parts.path.startswith(self.server.prefix):
            self.send_error(404)
            return
        relative = unquote(parts.path[len(self.server.prefix):]) or "index.html"
        path = (self.server.assets / relative).resolve()
        suffix = path.suffix.lower()
        if (not path.is_relative_to(self.server.assets) or not path.is_file()
                or suffix not in {".html", ".js", ".css", ".svg", ".png", ".ico", ".woff", ".woff2", ".txt"}
                or (suffix == ".html" and path != self.server.assets / "index.html")):
            self.send_error(404)
            return
        try:
            data = path.read_bytes()
        except OSError:
            self.send_error(404)
            return
        content_type = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}.get(suffix, mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_response(200)
        for name, value in {
            "Content-Type": content_type, "Content-Length": str(len(data)), "Content-Security-Policy": CSP,
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store", "Cross-Origin-Resource-Policy": "same-origin",
        }.items():
            self.send_header(name, value)
        self.end_headers()
        if body:
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass


class AssetServer:
    def __init__(self, assets=ASSETS):
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _AssetHandler)
        self._server.daemon_threads = True
        self._server.assets = Path(assets).resolve()
        self._server.expected_host = f"127.0.0.1:{self._server.server_port}"
        self._server.prefix = f"/{secrets.token_urlsafe(24)}/"
        self.url = f"http://{self._server.expected_host}{self._server.prefix}index.html"
        self._thread = threading.Thread(target=self._server.serve_forever, name="clearspace-assets", daemon=True)
        self._thread.start()

    def close(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


def _allowed_navigation(url, expected):
    value, target = urlsplit(str(url)), urlsplit(expected)
    return (value.scheme, value.netloc, value.path, value.query) == (target.scheme, target.netloc, target.path, target.query)


def _guard_navigation(window, expected):
    control = window.native.webview

    def navigation(_sender, args):
        if not _allowed_navigation(args.Uri, expected):
            args.Cancel = True

    def initialized(sender, args):
        if not args.IsSuccess:
            return

        def new_window(_sender, request):
            request.Handled = True

        sender.CoreWebView2.NewWindowRequested += new_window
        window._clearspace_popup_guard = new_window

    control.NavigationStarting += navigation
    control.CoreWebView2InitializationCompleted += initialized
    window._clearspace_navigation_guard = (navigation, initialized)


def _enable_per_monitor_dpi():
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes
    setter = ctypes.WinDLL("user32", use_last_error=True).SetProcessDpiAwarenessContext
    setter.argtypes = [ctypes.c_void_p]
    setter.restype = wintypes.BOOL
    setter(ctypes.c_void_p(-4))


def _windows_prefers_dark():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except (ImportError, OSError):
        return True


def _uses_dark_titlebar(theme):
    return theme == "dark" or (theme == "system" and _windows_prefers_dark())


def _apply_titlebar(window, dark):
    """Paint the native caption with the app colours; native window controls stay.

    Windows 11 accepts caption and text colours, so the caption blends
    into the app header. Windows 10 ignores them and keeps the dark/light mode.
    """
    try:
        import ctypes
        from ctypes import wintypes
        handle = wintypes.HWND(window.native.Handle.ToInt64())
        background, text = (0x000A0A0A, 0x00FAFAFA) if dark else (0x00FFFFFF, 0x000A0A0A)
        # 20 immersive dark mode, 35 caption, 36 caption text (COLORREF 0x00BBGGRR). The window border
        # keeps the Windows default so the window stays visible against dark backgrounds.
        for attribute, value in ((20, wintypes.BOOL(1 if dark else 0)), (35, wintypes.DWORD(background)),
                                 (36, wintypes.DWORD(text))):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(handle, attribute, ctypes.byref(value), ctypes.sizeof(value))
    except (AttributeError, OSError):
        pass


def _fatal(message):
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
    elif sys.stderr is not None:
        print(message, file=sys.stderr)


def main():
    multiprocessing.freeze_support()
    if os.name != "nt":
        _fatal("Clearspace currently supports Windows 10/11 only.")
        return 1
    _enable_per_monitor_dpi()
    if not (ASSETS / "index.html").is_file():
        _fatal("The interface is missing. Extract the complete portable package, or rebuild desktop-ui.")
        return 1
    for stream in ("stdout", "stderr"):
        if getattr(sys, stream) is None:
            setattr(sys, stream, open(os.devnull, "w", encoding="utf-8"))
    server, service, stop = None, None, threading.Event()
    try:
        import webview
        service = CleanerService()
        frozen = getattr(sys, "frozen", False)
        updater = updates.Updater(
            name="clearspace", repository="mighelsterzkdev/clearspace", version=__version__, executable="clearspace.exe",
            state_file=local_app_data() / "Clearspace" / "updates.json", install_dir=Path(sys.executable).resolve().parent if frozen else None,
        )
        api = DesktopApi(service, updater)
        server = AssetServer()
        webview.settings["ALLOW_DOWNLOADS"] = False
        webview.settings["ALLOW_FILE_URLS"] = False
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
        dark = _uses_dark_titlebar(service.theme())
        window = webview.create_window(
            APP_NAME, server.url, js_api=api, width=960, height=720,
            min_size=(760, 600), background_color="#0a0a0a" if dark else "#ffffff", text_select=True, frameless=os.name == "nt", easy_drag=False,
        )
        api.attach(window)
        window.events.before_show += partial(_guard_navigation, window, server.url)
        window.events.before_show += partial(_apply_titlebar, window, dark)
        window.events.before_show += partial(frame.use_custom_frame, window)
        feedback = shell.Feedback(window, APP_NAME)
        window.events.shown += lambda: threading.Thread(target=_watch, args=(service, feedback, stop), name="clearspace-feedback", daemon=True).start()
        window.events.shown += updater.check
        window.events.shown += updater.remove_leftovers

        def closing():
            if service.active() and not window.create_confirmation_dialog(
                "Close Clearspace?", "An operation is running. Stop it and close? Files already removed cannot be restored.",
            ):
                return False
            service.shutdown()
            return True

        window.events.closing += closing
        # An elevated WebView2 cannot share a profile with a standard one that may still be closing.
        storage = local_app_data() / "Clearspace" / ("webview2-admin" if is_administrator() else "webview2")
        storage.mkdir(parents=True, exist_ok=True)
        icon = Path(__file__).resolve().parents[2] / "assets" / "clearspace.ico"  # a packaged app uses its executable's icon
        webview.start(gui="edgechromium", debug=False, private_mode=True, storage_path=str(storage), icon=str(icon) if icon.is_file() else None)
        return 0
    except Exception:
        _fatal("Clearspace could not start. Check that the complete app is extracted and Microsoft Edge WebView2 Runtime is installed.")
        return 1
    finally:
        stop.set()
        if service is not None:
            service.shutdown()
        if server is not None:
            server.close()


if __name__ == "__main__":
    raise SystemExit(main())
