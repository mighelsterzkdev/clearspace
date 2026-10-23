"""Fixed Windows-owned maintenance operations, separate from file deletion.

No caller can provide a path, command or PowerShell source. Windows owns the
component store and Delivery Optimization cache; this module never traverses
those locations. Queries do not clean anything. The caller must confirm a
current analysis before ``clean``. An operation already submitted to Windows
cannot safely be cancelled: Stop prevents the next action, not servicing that
has started. Timeouts leave Windows running rather than terminating servicing.

References:
https://learn.microsoft.com/windows-hardware/manufacture/desktop/clean-up-the-winsxs-folder
https://learn.microsoft.com/windows-hardware/manufacture/desktop/determine-the-actual-size-of-the-winsxs-folder
https://learn.microsoft.com/windows-hardware/manufacture/desktop/dism-global-options-for-command-line-syntax
https://learn.microsoft.com/powershell/module/deliveryoptimization/delete-deliveryoptimizationcache
https://learn.microsoft.com/powershell/module/deliveryoptimization/get-deliveryoptimizationperfsnap
"""

from __future__ import annotations

import base64
import ctypes
from dataclasses import dataclass
import json
import os
import re
import subprocess
import tempfile
from threading import Event, Lock, Thread
from typing import Callable, Protocol

from . import windows


QUERY_TIMEOUT = 45
COMPONENT_CLEAN_TIMEOUT = 60 * 60
DELIVERY_CLEAN_TIMEOUT = 5 * 60
MAX_OUTPUT = 1024 * 1024
_ADMIN_REQUIRED = "Administrator permission required. Open Clearspace as administrator."
_NATIVE_DETAIL = "Windows determines the removable data. Once started, this system cleanup must finish before Stop takes effect."


class MaintenanceError(OSError):
    """A supported native maintenance action is unavailable or failed."""


@dataclass(frozen=True)
class ActionSnapshot:
    pending: bool
    bytes: int | None = None
    detail: str | None = None

    def __post_init__(self):
        if type(self.pending) is not bool:
            raise ValueError("An action's pending state must be a boolean.")
        if self.bytes is not None and (type(self.bytes) is not int or self.bytes < 0):
            raise ValueError("An action's byte estimate must be a nonnegative integer or unknown.")
        if self.detail is not None and not isinstance(self.detail, str):
            raise ValueError("An action's detail must be text or absent.")


@dataclass(frozen=True)
class ActionResult:
    completed: bool
    freed_bytes: int | None = None
    detail: str | None = None

    def __post_init__(self):
        if type(self.completed) is not bool:
            raise ValueError("An action's completion state must be a boolean.")
        if self.freed_bytes is not None and (type(self.freed_bytes) is not int or self.freed_bytes < 0):
            raise ValueError("Removed bytes must be a nonnegative integer or unknown.")
        if self.detail is not None and not isinstance(self.detail, str):
            raise ValueError("An action's detail must be text or absent.")


class MaintenanceProvider(Protocol):
    def query(self) -> ActionSnapshot: ...

    def clean(self, snapshot: ActionSnapshot, cancel: Event | None = None) -> ActionResult: ...


def is_administrator() -> bool:
    """Check the current token; never request elevation or display a window."""
    if os.name != "nt":
        return False
    try:
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        shell.IsUserAnAdmin.argtypes = []
        shell.IsUserAnAdmin.restype = ctypes.c_int
        return bool(shell.IsUserAnAdmin())
    except OSError:
        return False


def _restart_pending() -> bool:
    """Read CBS/Windows Update markers without altering Windows state."""
    if os.name != "nt":
        raise MaintenanceError("System cleanup requires Windows 10 or Windows 11.")
    import winreg

    paths = (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending",
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
    )
    for path in paths:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY):
                return True
        except FileNotFoundError:
            continue
        except OSError as error:
            raise MaintenanceError("Windows restart status could not be checked.") from error
    return False


@dataclass(frozen=True)
class _CommandResult:
    returncode: int
    output: str


class _CommandRunner:
    """Serialize native work and keep timed-out servicing alive, with no pipe hang.

    Output goes to an owned temporary file, never an arbitrary log path. A
    daemon reaper retains that handle after a timeout until the child exits.
    Further operations are rejected while the previous child is still running.
    """

    def __init__(self):
        self._lock = Lock()
        self._active = None

    def _reap(self, child, output):
        try:
            child.wait()
        finally:
            output.close()
            with self._lock:
                if self._active is child:
                    self._active = None

    def run(self, command: tuple[str, ...], *, timeout: int, powershell: bool = False) -> _CommandResult:
        if os.name != "nt":
            raise MaintenanceError("System cleanup requires Windows 10 or Windows 11.")
        with self._lock:
            if self._active is not None:
                raise MaintenanceError("A Windows maintenance operation is still running. Wait for it to finish before trying again.")
            output = tempfile.TemporaryFile(mode="w+b")
            try:
                system_dir = windows.windows_directory() / "System32"
                environment = os.environ.copy()
                environment["SystemRoot"] = str(windows.windows_directory())
                environment["WINDIR"] = str(windows.windows_directory())
                # -NoProfile and a Windows-only module search path prevent user
                # startup code/modules from changing a fixed maintenance action.
                if powershell:
                    environment["PSModulePath"] = str(system_dir / "WindowsPowerShell" / "v1.0" / "Modules")
                startup = subprocess.STARTUPINFO()
                startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startup.wShowWindow = subprocess.SW_HIDE
                child = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL, stdout=output,
                    stderr=subprocess.STDOUT, cwd=system_dir, env=environment,
                    shell=False, close_fds=True, startupinfo=startup,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                self._active = child
            except OSError as error:
                output.close()
                raise MaintenanceError("Windows could not start the system cleanup tool.") from error
        try:
            child.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            Thread(target=self._reap, args=(child, output), daemon=True,
                   name="clearspace-windows-maintenance").start()
            raise MaintenanceError("Windows maintenance is taking longer than expected and is still running. It was not stopped. Wait for it to finish before trying again.") from error
        try:
            output.seek(0)
            raw = output.read(MAX_OUTPUT + 1)
            if len(raw) > MAX_OUTPUT:
                raise MaintenanceError("Windows returned an unexpectedly large maintenance report.")
            # DISM /English uses the OEM console code page; fields we parse are
            # ASCII. Our fixed PowerShell scripts explicitly write UTF-8.
            text = raw.decode("utf-8" if powershell else "oem", errors="replace")
            return _CommandResult(child.returncode, text.lstrip("\ufeff"))
        finally:
            output.close()
            with self._lock:
                if self._active is child:
                    self._active = None


_RUNNER = _CommandRunner()


def _fixed_system_executable(relative: tuple[str, ...]) -> str:
    return str(windows.windows_directory().joinpath("System32", *relative))


def _validate_snapshot(snapshot: ActionSnapshot) -> None:
    if type(snapshot) is not ActionSnapshot:
        raise MaintenanceError("Analyze this system cleanup again before continuing.")


def _cancelled(cancel: Event | None) -> bool:
    return cancel is not None and cancel.is_set()


def _stopped() -> ActionResult:
    return ActionResult(False, None, "Stopped before Windows system cleanup started.")


class WindowsComponentCleanup:
    """DISM owns superseded component cleanup; ResetBase is never used.

    The component store's size/overhead is not a reclaimable byte estimate.
    ``bytes`` and ``freed_bytes`` therefore remain unknown. A fresh native
    analysis is required immediately before cleanup, as well as at scan time.
    ``runner``/``administrator``/``restart_pending`` are internal test seams.
    """

    action_id = "windows_components"

    def __init__(self, *, runner=None, administrator: Callable[[], bool] | None = None,
                 restart_pending: Callable[[], bool] | None = None):
        self._runner = runner if runner is not None else _RUNNER
        self._administrator = administrator or is_administrator
        self._restart_pending = restart_pending or _restart_pending

    def _preflight(self):
        if not self._administrator():
            raise MaintenanceError(_ADMIN_REQUIRED)
        if self._restart_pending():
            raise MaintenanceError("Restart Windows to finish pending updates before cleaning Windows components.")

    def _command(self, operation: str) -> tuple[str, ...]:
        # All callers are internal and operations are allowlisted here as well.
        if operation not in ("/AnalyzeComponentStore", "/StartComponentCleanup"):
            raise ValueError("Unsupported Windows component operation.")
        return (_fixed_system_executable(("dism.exe",)), "/Online", "/Cleanup-Image",
                operation, "/English", "/NoRestart")

    def query(self) -> ActionSnapshot:
        self._preflight()
        result = self._runner.run(self._command("/AnalyzeComponentStore"), timeout=QUERY_TIMEOUT)
        if result.returncode == 3010:
            raise MaintenanceError("Restart Windows before cleaning Windows components.")
        if result.returncode != 0:
            raise MaintenanceError(f"Windows could not analyze component cleanup (code {result.returncode & 0xFFFFFFFF}).")
        match = re.search(r"^\s*Component Store Cleanup Recommended\s*:\s*(Yes|No)\s*$",
                          result.output, re.MULTILINE | re.IGNORECASE)
        if match is None:
            raise MaintenanceError("Windows returned an unrecognized component cleanup report. No component cleanup was authorized.")
        pending = match.group(1).lower() == "yes"
        return ActionSnapshot(pending, None, _NATIVE_DETAIL if pending else "Windows does not recommend component cleanup right now.")

    def clean(self, snapshot: ActionSnapshot, cancel: Event | None = None) -> ActionResult:
        _validate_snapshot(snapshot)
        if _cancelled(cancel):
            return _stopped()
        if not snapshot.pending:
            return ActionResult(True, 0, "No Windows component cleanup was needed.")
        current = self.query()
        if _cancelled(cancel):
            return _stopped()
        if not current.pending:
            return ActionResult(True, 0, "Windows no longer recommends component cleanup.")
        result = self._runner.run(self._command("/StartComponentCleanup"),
                                  timeout=COMPONENT_CLEAN_TIMEOUT)
        if result.returncode not in (0, 3010):
            raise MaintenanceError(f"Windows component cleanup failed (code {result.returncode & 0xFFFFFFFF}).")
        detail = ("Windows component cleanup completed. Restart Windows to finish."
                  if result.returncode == 3010 else "Windows component cleanup completed. Windows does not report an exact removed size.")
        return ActionResult(True, None, detail)


# -Force skips the cmdlet's own RawUI.ReadKey prompt after app confirmation;
# it does not enable deleting pinned files. Never pass -IncludePinnedFiles.
# The installed Microsoft module exposes CacheSizeBytes via the perf snapshot.
_POWERSHELL_PREFIX = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Import-Module (Join-Path $PSHOME 'Modules\DeliveryOptimization\DeliveryOptimization.psd1') -DisableNameChecking -ErrorAction Stop
""".strip()
_DELIVERY_QUERY = _POWERSHELL_PREFIX + r"""
$null = Get-Command 'DeliveryOptimization\Delete-DeliveryOptimizationCache' -ErrorAction Stop
$snapshot = DeliveryOptimization\Get-DeliveryOptimizationPerfSnap -ErrorAction Stop
if ($null -eq $snapshot) { throw 'Delivery Optimization did not return a cache report.' }
$cacheBytes = $null
if ($null -ne $snapshot.PSObject.Properties['CacheSizeBytes']) {
    $cacheBytes = [UInt64]$snapshot.CacheSizeBytes
}
[pscustomobject]@{ bytes = $cacheBytes } | ConvertTo-Json -Compress
"""
_DELIVERY_CLEAN = _POWERSHELL_PREFIX + r"""
DeliveryOptimization\Delete-DeliveryOptimizationCache -Force -ErrorAction Stop | Out-Null
"""


class WindowsDeliveryOptimization:
    """Remove only the cache Windows permits, retaining pinned downloads."""

    action_id = "delivery_optimization"

    def __init__(self, *, runner=None, administrator: Callable[[], bool] | None = None):
        self._runner = runner if runner is not None else _RUNNER
        self._administrator = administrator or is_administrator

    def _run(self, script: str, timeout: int) -> _CommandResult:
        # The only scripts passed here are the two module constants above.
        if script not in (_DELIVERY_QUERY, _DELIVERY_CLEAN):
            raise ValueError("Unsupported Delivery Optimization operation.")
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        command = (_fixed_system_executable(("WindowsPowerShell", "v1.0", "powershell.exe")),
                   "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded)
        return self._runner.run(command, timeout=timeout, powershell=True)

    def query(self) -> ActionSnapshot:
        if not self._administrator():
            raise MaintenanceError(_ADMIN_REQUIRED)
        result = self._run(_DELIVERY_QUERY, QUERY_TIMEOUT)
        if result.returncode != 0:
            raise MaintenanceError("Delivery Optimization cache could not be read. The Windows service or PowerShell module may be unavailable.")
        try:
            report = json.loads(result.output)
            size = report["bytes"]
            if size is not None and (type(size) is not int or size < 0):
                raise ValueError("Invalid cache bytes")
        except (ValueError, TypeError, KeyError) as error:
            raise MaintenanceError("Windows returned an invalid Delivery Optimization cache report.") from error
        return ActionSnapshot(size is None or size > 0, size,
                              "Cache size includes data Windows may retain. Pinned files are kept. " + _NATIVE_DETAIL)

    def clean(self, snapshot: ActionSnapshot, cancel: Event | None = None) -> ActionResult:
        _validate_snapshot(snapshot)
        if _cancelled(cancel):
            return _stopped()
        if not snapshot.pending:
            return ActionResult(True, 0, "Delivery Optimization cache was empty.")
        current = self.query()
        if _cancelled(cancel):
            return _stopped()
        if not current.pending:
            return ActionResult(True, 0, "Delivery Optimization cache is already empty.")
        result = self._run(_DELIVERY_CLEAN, DELIVERY_CLEAN_TIMEOUT)
        if result.returncode != 0:
            raise MaintenanceError("Windows could not complete Delivery Optimization cleanup. Pinned files were not force-deleted.")
        return ActionResult(True, None, "Windows completed Delivery Optimization cleanup. Pinned files were kept; an exact removed size is unavailable.")
