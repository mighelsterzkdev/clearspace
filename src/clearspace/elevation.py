"""Ask Windows for administrator permission before starting the desktop app.

Only this app's own executable, or its own source interpreter/module, can be
relaunched. Caller arguments, paths and shell commands are never forwarded.
The fixed relaunch marker prevents repeated UAC prompts if Windows does not
grant an administrator token. Importing this module never requests elevation.
The portable --self-check entry point bypasses the GUI and this helper.

References:
https://learn.microsoft.com/windows/win32/api/shellapi/nf-shellapi-shellexecuteexw
https://learn.microsoft.com/windows/win32/api/shellapi/ns-shellapi-shellexecuteinfow
https://learn.microsoft.com/windows/win32/shell/launch
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
from typing import Callable

from .maintenance import is_administrator


_RELAUNCH_MARKER = "--clearspace-elevated"
_ERROR_CANCELLED = 1223
_SEE_MASK_NOASYNC = 0x00000100
_SEE_MASK_FLAG_NO_UI = 0x00000400
_SEE_MASK_UNICODE = 0x00004000
_SW_SHOWNORMAL = 1
_COINIT_APARTMENTTHREADED = 0x2
_COINIT_DISABLE_OLE1DDE = 0x4
_RPC_E_CHANGED_MODE = 0x80010106


class ElevationError(OSError):
    """Windows could not start Clearspace with the required permission."""


class _SHELLEXECUTEINFOW(ctypes.Structure):
    # Windows DWORD/ULONG remain 32 bits in a 64-bit process.
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("fMask", ctypes.c_uint32),
        ("hwnd", ctypes.c_void_p),
        ("lpVerb", ctypes.c_wchar_p),
        ("lpFile", ctypes.c_wchar_p),
        ("lpParameters", ctypes.c_wchar_p),
        ("lpDirectory", ctypes.c_wchar_p),
        ("nShow", ctypes.c_int),
        ("hInstApp", ctypes.c_void_p),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", ctypes.c_wchar_p),
        ("hkeyClass", ctypes.c_void_p),
        ("dwHotKey", ctypes.c_uint32),
        ("hIcon", ctypes.c_void_p),
        ("hProcess", ctypes.c_void_p),
    ]


@dataclass(frozen=True)
class _RelaunchCommand:
    executable: str
    arguments: tuple[str, ...]
    directory: str


def _relaunch_command() -> _RelaunchCommand:
    executable = Path(sys.executable)
    if not executable.is_absolute():
        raise ElevationError("Clearspace could not locate its application executable.")
    executable = executable.resolve()
    if getattr(sys, "frozen", False):
        arguments = (_RELAUNCH_MARKER,)
    else:
        # A windowless interpreter avoids opening another terminal. Isolated
        # mode ignores PYTHONPATH and the working directory when importing the
        # installed source package; the project's editable install still works.
        executable = executable.with_name("pythonw.exe")
        arguments = ("-I", "-B", "-m", "clearspace.gui", _RELAUNCH_MARKER)
    if executable.suffix.lower() != ".exe" or not executable.is_file():
        raise ElevationError("The Clearspace application runtime is missing. Extract the complete package or run the setup script again.")
    return _RelaunchCommand(str(executable), arguments, str(executable.parent))


def _shell_api():
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFOW)]
    shell.ShellExecuteExW.restype = ctypes.c_int
    return shell


def _ole_api():
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    ole.CoInitializeEx.restype = ctypes.c_int32
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    return ole


def _request_elevation(command: _RelaunchCommand, *, shell=None, ole=None) -> int:
    """Return zero for a launch, otherwise a Win32 error; never run a shell."""
    shell = shell if shell is not None else _shell_api()
    ole = ole if ole is not None else _ole_api()
    initialized = ole.CoInitializeEx(None, _COINIT_APARTMENTTHREADED | _COINIT_DISABLE_OLE1DDE)
    code = initialized & 0xFFFFFFFF
    if code not in (0, 1, _RPC_E_CHANGED_MODE):
        raise ElevationError("Windows could not prepare the administrator permission request.")
    try:
        info = _SHELLEXECUTEINFOW()
        info.cbSize = ctypes.sizeof(info)
        # NOASYNC is required because this initial process exits after relaunch.
        # NO_UI suppresses redundant failure dialogs, while preserving UAC.
        info.fMask = _SEE_MASK_NOASYNC | _SEE_MASK_FLAG_NO_UI | _SEE_MASK_UNICODE
        info.lpVerb = "runas"
        info.lpFile = command.executable
        info.lpParameters = subprocess.list2cmdline(command.arguments)
        info.lpDirectory = command.directory
        info.nShow = _SW_SHOWNORMAL
        ctypes.set_last_error(0)
        if shell.ShellExecuteExW(ctypes.byref(info)):
            return 0
        return ctypes.get_last_error() or 1
    finally:
        if code in (0, 1):
            ole.CoUninitialize()


def ensure_administrator(*, administrator: Callable[[], bool] | None = None,
                         request: Callable[[_RelaunchCommand], int] | None = None) -> bool:
    """Return True to start the GUI, False after relaunch or UAC cancellation.

    ``administrator`` and ``request`` are internal test seams. Production calls
    take no arguments, so neither the renderer nor the command line can choose
    an executable to elevate. Cancellation closes the initial app quietly.
    """
    if os.name != "nt":
        raise ElevationError("Clearspace currently supports Windows 10/11 only.")
    administrator = administrator or is_administrator
    if administrator():
        return True
    if _RELAUNCH_MARKER in sys.argv[1:]:
        raise ElevationError("Windows did not grant administrator permission. Open Clearspace again and approve the Windows permission request.")
    try:
        command = _relaunch_command()
        result = (request or _request_elevation)(command)
    except ElevationError:
        raise
    except OSError as error:
        raise ElevationError("Windows could not request administrator permission. Open Clearspace again or use Run as administrator.") from error
    if result in (0, _ERROR_CANCELLED):
        return False
    raise ElevationError(f"Windows could not start Clearspace as administrator (code {result & 0xFFFFFFFF}). Open the app again or use Run as administrator.")
