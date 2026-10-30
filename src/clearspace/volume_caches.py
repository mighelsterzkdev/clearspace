"""Windows Disk Cleanup handlers (IEmptyVolumeCache), the same cleaners cleanmgr.exe runs.

Every handler registered under ``VolumeCaches`` measures and removes its own data, so the
categories match what Windows Disk Cleanup offers on that computer. Clearspace only calls
them: it never passes paths, and it skips handlers that would remove personal files or
duplicate a Clearspace category. Each call runs on its own single-threaded COM apartment.

References:
https://learn.microsoft.com/windows/win32/lwef/disk-cleanup
https://learn.microsoft.com/windows/win32/api/emptyvc/nn-emptyvc-iemptyvolumecache2
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from functools import lru_cache
import os
import re
import threading

ROOT = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\VolumeCaches"
# Personal data (the Downloads folder, File History versions), enterprise peer caching, or data
# that Clearspace already cleans through its own categories.
EXCLUDED = frozenset(key.casefold() for key in (
    "DownloadsFolder", "User file versions", "BranchCache",
    "Recycle Bin", "Temporary Files", "Thumbnail Cache", "D3D Shader Cache", "Delivery Optimization Files", "Update Cleanup",
    "Windows Error Reporting Files", "System error memory dump files", "System error minidump files", "Setup Log Files",
))
# The Internet Explorer era handlers (WinINet) and sync files reject or fault inside InitializeEx; they use the
# original Initialize. Every other handler is initialized with InitializeEx, as Disk Cleanup does:
# shared components need its key name, and some (driver packages) fault in Initialize.
INITIALIZE_ONLY = frozenset(key.casefold() for key in ("Internet Cache Files", "Offline Pages Files", "Temporary Sync Files"))
# Removing these gives up a way back (rolling back an upgrade, reinstalling a driver offline).
CONSEQUENTIAL = frozenset(key.casefold() for key in ("Previous Installations", "Windows ESD installation files", "Device Driver Packages"))

PREFIX = "disk_cleanup_"
# Plain names and descriptions for the handlers Windows ships; unknown handlers keep Windows' own text.
KNOWN = {key.casefold(): value for key, value in {
    "Active Setup Temp Folders": ("Temporary setup files", "Files left behind by program installers."),
    "Content Indexer Cleaner": ("Old search index files", "Leftovers from the previous Windows indexing service."),
    "Device Driver Packages": ("Old driver packages", "Older copies of installed drivers. Current drivers keep working, but you can no longer roll back to these versions."),
    "Diagnostic Data Viewer database files": ("Diagnostic Data Viewer database", "Diagnostic data stored for the Diagnostic Data Viewer."),
    "Downloaded Program Files": ("Downloaded program files", "ActiveX controls and Java applets downloaded by old web pages."),
    "Feedback Hub Archive log files": ("Feedback Hub logs", "Diagnostic logs saved by Feedback Hub."),
    "Internet Cache Files": ("Temporary Internet files", "Web content cached by Windows and Internet Explorer mode. Sign-ins and favorites are kept."),
    "Language Pack": ("Unused language resources", "Language files Windows no longer uses."),
    "Offline Pages Files": ("Offline web pages", "Web pages saved for reading offline."),
    "Old ChkDsk Files": ("Old disk check fragments", "File fragments recovered by earlier disk checks."),
    "Previous Installations": ("Previous Windows installation", "Windows.old. You will no longer be able to go back to the previous version of Windows."),
    "RetailDemo Offline Content": ("Store demo content", "Demonstration files used by computers on display in stores."),
    "Temporary Setup Files": ("Temporary Windows installation files", "Files left behind by Windows setup."),
    "Temporary Sync Files": ("Temporary sync files", "Leftover files from Windows sync."),
    "Upgrade Discarded Files": ("Files discarded by Windows upgrade", "Files Windows set aside during an upgrade and no longer needs."),
    "Windows Defender": ("Microsoft Defender files", "Non-critical Defender files, such as old scan logs."),
    "Windows ESD installation files": ("Windows installation files", "Used to reset Windows from local files. A reset can download them again."),
    "Windows Reset Log Files": ("System recovery logs", "Logs from Windows reset and recovery."),
    "Windows Upgrade Log Files": ("Windows upgrade logs", "Logs from Windows upgrades."),
}.items()}
# Preselected like Disk Cleanup's own defaults; personal-looking or consequential data is not.
RECOMMENDED = frozenset(key.casefold() for key in (
    "Active Setup Temp Folders", "Content Indexer Cleaner", "Diagnostic Data Viewer database files", "Downloaded Program Files",
    "Feedback Hub Archive log files", "Internet Cache Files", "Old ChkDsk Files", "RetailDemo Offline Content", "Temporary Setup Files",
    "Temporary Sync Files", "Upgrade Discarded Files", "Windows Defender", "Windows Reset Log Files", "Windows Upgrade Log Files",
))

_S_OK, _S_FALSE = 0, 1
_E_ABORT = 0x80004004 - (1 << 32)
_E_NOINTERFACE = 0x80004002 - (1 << 32)
_HKEY_LOCAL_MACHINE = ctypes.c_void_p(0xFFFFFFFF80000002 if ctypes.sizeof(ctypes.c_void_p) == 8 else 0x80000002)  # sign-extended predefined key
_KEY_READ = 0x20019
_IID_CACHE = "{8FCE5227-04DA-11D1-A004-00805F8ABE06}"
_IID_CACHE2 = "{02B7E3BA-4DB3-11D2-B2D9-00C04F8EEC8C}"
_IID_CALLBACK = "{6E793361-73C6-11D0-8469-00AA00442901}"
_IID_UNKNOWN = "{00000000-0000-0000-C000-000000000046}"


class VolumeCacheError(OSError):
    """A Windows cleanup handler could not run."""


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]


@dataclass(frozen=True)
class Handler:
    key: str
    clsid: str
    title: str
    description: str


def category_id(key):
    return PREFIX + re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")


@lru_cache(maxsize=1)
def by_category():
    """Category id to handler for this session. Ids only ever come from the registry."""
    return {category_id(handler.key): handler for handler in handlers()}


def _guid(text):
    value = _GUID()
    ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(value))
    return value


def _same_guid(pointer, text):
    return ctypes.string_at(pointer, ctypes.sizeof(_GUID)) == bytes(_guid(text))


def _indirect(value):
    """Resolve '@dll,-id' resource strings the way Disk Cleanup does."""
    if not isinstance(value, str) or not value.startswith("@"):
        return value if isinstance(value, str) else ""
    buffer = ctypes.create_unicode_buffer(1024)
    try:
        ctypes.oledll.shlwapi.SHLoadIndirectString(value, buffer, len(buffer), None)
        return buffer.value
    except OSError:
        return ""


def handlers():
    """Registered handlers that Clearspace offers, with names from the registry."""
    if os.name != "nt":
        return []
    import winreg
    found = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, ROOT)
    except OSError:
        return []
    with root:
        index = 0
        while True:
            try:
                key = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            if key.casefold() in EXCLUDED:
                continue
            try:
                with winreg.OpenKey(root, key) as item:
                    clsid = winreg.QueryValueEx(item, "")[0]
                    values = {}
                    for name in ("Display", "Description"):
                        try:
                            values[name] = winreg.QueryValueEx(item, name)[0]
                        except OSError:
                            values[name] = ""
            except OSError:
                continue
            if isinstance(clsid, str) and clsid.startswith("{"):
                found.append(Handler(key, clsid, _indirect(values["Display"]) or key, _indirect(values["Description"])))
    return sorted(found, key=lambda item: item.key.casefold())


# --- COM plumbing -----------------------------------------------------------------------

if os.name == "nt":
    _QI = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
    _REF = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
    _SCAN = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_ulonglong, wintypes.DWORD, ctypes.c_wchar_p)
    _PURGE = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_ulonglong, ctypes.c_ulonglong, wintypes.DWORD, ctypes.c_wchar_p)


class _Callback:
    """A minimal IEmptyVolumeCacheCallBack: returns E_ABORT once Stop is requested."""

    def __init__(self, cancel):
        self._cancel = cancel

        def query(this, iid, out):
            if _same_guid(iid, _IID_CALLBACK) or _same_guid(iid, _IID_UNKNOWN):
                out[0] = this
                return _S_OK
            out[0] = None
            return _E_NOINTERFACE

        self._functions = (_QI(query), _REF(lambda this: 1), _REF(lambda this: 1),
                           _SCAN(lambda this, used, flags, status: self._result()),
                           _PURGE(lambda this, freed, total, flags, status: self._result()))
        self._vtable = (ctypes.c_void_p * 5)(*(ctypes.cast(function, ctypes.c_void_p) for function in self._functions))
        self._object = ctypes.c_void_p(ctypes.addressof(self._vtable))
        self.pointer = ctypes.addressof(self._object)

    def _result(self):
        return _E_ABORT if self._cancel is not None and self._cancel.is_set() else _S_OK


def _method(pointer, index, *argtypes):
    vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtable[index])


def _release(pointer):
    if pointer:
        vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])(pointer)


def _in_apartment(function):
    """Run ``function`` on a fresh STA thread; handlers are apartment-threaded COM objects."""
    outcome = {}

    def run():
        ole = ctypes.windll.ole32
        initialized = ole.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
        try:
            outcome["value"] = function()
        except BaseException as error:  # reported on the calling thread
            outcome["error"] = error
        finally:
            if initialized in (0, 1):
                ole.CoUninitialize()

    thread = threading.Thread(target=run, name="clearspace-volume-cache", daemon=True)
    thread.start()
    thread.join()
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def _open(handler, volume):
    """Create and initialize a handler. Returns the cache object, or None when it has nothing to offer."""
    advapi = ctypes.windll.advapi32
    advapi.RegOpenKeyExW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    advapi.RegCloseKey.argtypes = [ctypes.c_void_p]
    key = ctypes.c_void_p()
    if advapi.RegOpenKeyExW(_HKEY_LOCAL_MACHINE, f"{ROOT}\\{handler.key}", 0, _KEY_READ, ctypes.byref(key)):
        raise VolumeCacheError("Windows could not read this cleanup handler.")
    unknown, cache = ctypes.c_void_p(), ctypes.c_void_p()
    try:
        try:
            ctypes.oledll.ole32.CoCreateInstance(ctypes.byref(_guid(handler.clsid)), None, 0x1 | 0x4,  # in-process or local server
                                                 ctypes.byref(_guid(_IID_UNKNOWN)), ctypes.byref(unknown))
        except OSError as error:
            raise VolumeCacheError("Windows could not start this cleanup handler.") from error
        display, description, button, flags = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p(), wintypes.DWORD()
        query = _method(unknown, 0, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
        if handler.key.casefold() not in INITIALIZE_ONLY and query(unknown, ctypes.byref(_guid(_IID_CACHE2)), ctypes.byref(cache)) == _S_OK:
            result = _method(cache, 8, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
                             ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))(
                cache, key, volume, handler.key, ctypes.byref(display), ctypes.byref(description), ctypes.byref(button), 0, ctypes.byref(flags))
        elif query(unknown, ctypes.byref(_guid(_IID_CACHE)), ctypes.byref(cache)) == _S_OK:
            result = _method(cache, 3, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
                             ctypes.POINTER(wintypes.DWORD))(cache, key, volume, ctypes.byref(display), ctypes.byref(description), ctypes.byref(flags))
        else:
            raise VolumeCacheError("This cleanup handler is not supported.")
        for text in (display, description, button):
            if text:
                ctypes.windll.ole32.CoTaskMemFree(text)
        if result == _S_FALSE:
            _release(cache)
            return None
        if result < 0:
            _release(cache)
            raise VolumeCacheError(f"Windows could not prepare this cleanup (code 0x{result & 0xFFFFFFFF:08X}).")
        return cache
    finally:
        _release(unknown)
        advapi.RegCloseKey(key)


def _deactivate(cache):
    flags = wintypes.DWORD()
    try:
        _method(cache, 7, ctypes.POINTER(wintypes.DWORD))(cache, ctypes.byref(flags))
    finally:
        _release(cache)


def _space_used(cache, callback):
    used = ctypes.c_ulonglong()
    result = _method(cache, 4, ctypes.POINTER(ctypes.c_ulonglong), ctypes.c_void_p)(cache, ctypes.byref(used), callback.pointer)
    if result < 0 and result != _E_ABORT:
        raise VolumeCacheError(f"Windows could not measure this cleanup (code 0x{result & 0xFFFFFFFF:08X}).")
    return used.value


def measure(handler, volume="C:\\", cancel=None):
    """Bytes the handler would free, or None when Windows has nothing to offer for it."""
    def run():
        cache = _open(handler, volume)
        if cache is None:
            return None
        try:
            return _space_used(cache, _Callback(cancel))
        finally:
            _deactivate(cache)
    return _in_apartment(run)


def purge(handler, volume="C:\\", cancel=None):
    """Let the handler remove its data. Returns the size Windows measured just before."""
    def run():
        cache = _open(handler, volume)
        if cache is None:
            return 0
        try:
            callback = _Callback(cancel)
            used = _space_used(cache, callback)
            if cancel is not None and cancel.is_set():
                return 0
            result = _method(cache, 5, ctypes.c_ulonglong, ctypes.c_void_p)(cache, used, callback.pointer)
            if result < 0 and result != _E_ABORT:
                raise VolumeCacheError(f"Windows could not finish this cleanup (code 0x{result & 0xFFFFFFFF:08X}).")
            return used
        finally:
            _deactivate(cache)
    return _in_apartment(run)


class Provider:
    """Native action adapter: Windows measures, then removes, the handler's data."""

    def __init__(self, handler, *, administrator=None):
        self._handler = handler
        if administrator is None:
            from .maintenance import is_administrator as administrator
        self._administrator = administrator

    def query(self, cancel=None):
        from .maintenance import ActionSnapshot
        size = measure(self._handler, cancel=cancel)
        if size is None:
            # Disk Cleanup lists several handlers only in its elevated "system files" mode.
            detail = None if self._administrator() else "Windows offers this cleanup with administrator permission."
            return ActionSnapshot(False, 0, detail)
        return ActionSnapshot(size > 0, size)

    def clean(self, snapshot, cancel=None):
        from .maintenance import ActionResult
        if cancel is not None and cancel.is_set():
            return ActionResult(False, None, "Cleanup stopped before this action started.")
        freed = purge(self._handler, cancel=cancel)
        if cancel is not None and cancel.is_set():
            return ActionResult(False, None, "Cleanup stopped. Windows may have removed part of this data.")
        return ActionResult(True, freed)
