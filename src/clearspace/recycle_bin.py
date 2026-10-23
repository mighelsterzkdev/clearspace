"""Native Recycle Bin operations for the current Windows user.

The Shell owns the Recycle Bin layout and deletion semantics. No path from the
renderer is accepted, and this adapter never traverses or unlinks $Recycle.Bin.
Query is read-only; empty is a separate, irreversible operation. The caller must
obtain explicit confirmation before calling empty. A snapshot is an aggregate,
not a list of file identities: empty removes the contents present when Windows
performs the operation, including folders and any newly recycled items.

These synchronous Shell entry points take no shared COM interface pointers and
can be called from the service's worker thread. Calls on one provider are
serialized. A running native empty operation cannot be interrupted safely.

API references:
https://learn.microsoft.com/en-us/windows/win32/api/shellapi/ns-shellapi-shqueryrbinfo
https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shqueryrecyclebinw
https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shemptyrecyclebinw
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from functools import lru_cache
import os
from threading import Lock
from typing import Protocol


SHERB_NOCONFIRMATION = 0x00000001
SHERB_NOPROGRESSUI = 0x00000002
SHERB_NOSOUND = 0x00000004
_EMPTY_FLAGS = SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND


class RecycleBinError(OSError):
    """Windows could not read or empty the current user's Recycle Bin."""

    def __init__(self, message: str, *, hresult: int | None = None):
        super().__init__(message)
        self.hresult = hresult & 0xFFFFFFFF if hresult is not None else None


@dataclass(frozen=True)
class RecycleBinSnapshot:
    items: int
    bytes: int

    def __post_init__(self):
        if (type(self.items) is not int or type(self.bytes) is not int
                or self.items < 0 or self.bytes < 0):
            raise ValueError("Recycle Bin totals must be nonnegative integers.")


class RecycleBinProvider(Protocol):
    def query(self) -> RecycleBinSnapshot: ...

    def empty(self) -> None: ...


class _SHQUERYRBINFO(ctypes.Structure):
    # DWORD is always 32 bits; the signed 64-bit fields retain native alignment.
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("i64Size", ctypes.c_int64),
        ("i64NumItems", ctypes.c_int64),
    ]


@lru_cache(maxsize=1)
def _shell32():
    if os.name != "nt":
        raise RecycleBinError("Recycle Bin cleanup requires Windows 10 or Windows 11.")
    api = ctypes.WinDLL("shell32", use_last_error=True)
    api.SHQueryRecycleBinW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(_SHQUERYRBINFO)]
    api.SHQueryRecycleBinW.restype = ctypes.c_int32
    api.SHEmptyRecycleBinW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
    api.SHEmptyRecycleBinW.restype = ctypes.c_int32
    return api


def _check_result(result: int, operation: str) -> None:
    # HRESULT is signed 32-bit even in a 64-bit process. Test fakes may supply
    # either its signed or unsigned Python representation.
    code = result & 0xFFFFFFFF
    if not code & 0x80000000:
        return
    action = "read" if operation == "query" else "empty"
    if code == 0x80070005:
        reason = "Access was denied."
    elif code == 0x800704C7:
        reason = "The operation was cancelled."
    else:
        reason = f"Windows error 0x{code:08X}."
    raise RecycleBinError(f"Windows could not {action} the Recycle Bin. {reason}",
                          hresult=code)


class WindowsRecycleBin:
    """Current user's Recycle Bins on all drives, managed through Shell32.

    ``api`` is an internal native-call test seam. Production callers use the
    default constructor; neither method accepts a drive, path, or shell command.
    An empty call deliberately raises native errors instead of assuming success.
    Windows can report failure when another program has already emptied the bin,
    so callers may query again to determine the current state.
    """

    def __init__(self, *, api=None):
        self._api = api
        self._lock = Lock()

    def query(self) -> RecycleBinSnapshot:
        """Read aggregate item count and logical bytes without modifying data."""
        with self._lock:
            info = _SHQUERYRBINFO()
            info.cbSize = ctypes.sizeof(info)
            try:
                api = self._api if self._api is not None else _shell32()
                result = api.SHQueryRecycleBinW(None, ctypes.byref(info))
            except RecycleBinError:
                raise
            except OSError as error:
                raise RecycleBinError("Windows could not read the Recycle Bin.") from error
            _check_result(result, "query")
            if info.i64NumItems < 0 or info.i64Size < 0:
                raise RecycleBinError("Windows returned invalid Recycle Bin totals.")
            return RecycleBinSnapshot(items=info.i64NumItems, bytes=info.i64Size)

    def empty(self) -> None:
        """Permanently empty current contents after the caller's confirmation."""
        with self._lock:
            try:
                api = self._api if self._api is not None else _shell32()
                result = api.SHEmptyRecycleBinW(None, None, _EMPTY_FLAGS)
            except RecycleBinError:
                raise
            except OSError as error:
                raise RecycleBinError("Windows could not empty the Recycle Bin.") from error
            _check_result(result, "empty")
