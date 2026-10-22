"""Small, fail-closed Windows API layer used by the cleanup engine.

Every directory from the drive root is opened without data-write/delete sharing.
These handles stay open while resolving descendants, preventing ancestor
renames. Each opened component and final file must also match its normalized
expected path. Reparse points are excluded. A deletion uses the validated
*file handle*, never unlink.

API references:
https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew
https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle
https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getfinalpathnamebyhandlew
https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_id_info
https://learn.microsoft.com/en-us/windows/win32/shell/known-folders
"""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path
from typing import Iterator
import uuid


FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_READ_DATA = 0x1  # The same access bit means FILE_LIST_DIRECTORY on a directory.
FILE_READ_ATTRIBUTES = 0x80
DELETE = 0x10000
FILE_SHARE_READ = 0x1
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
WINDOWS_EPOCH_TICKS = 116444736000000000


class UnsafePathError(OSError):
    """The file cannot be addressed under the supported path policy."""


class _FILE_BASIC_INFO(ctypes.Structure):
    _fields_ = [
        ("CreationTime", ctypes.c_longlong),
        ("LastAccessTime", ctypes.c_longlong),
        ("LastWriteTime", ctypes.c_longlong),
        ("ChangeTime", ctypes.c_longlong),
        ("FileAttributes", wintypes.DWORD),
    ]


class _FILE_STANDARD_INFO(ctypes.Structure):
    _fields_ = [
        ("AllocationSize", ctypes.c_longlong),
        ("EndOfFile", ctypes.c_longlong),
        ("NumberOfLinks", wintypes.DWORD),
        ("DeletePending", ctypes.c_ubyte),
        ("Directory", ctypes.c_ubyte),
    ]


class _FILE_ID_INFO(ctypes.Structure):
    _fields_ = [
        ("VolumeSerialNumber", ctypes.c_ulonglong),
        ("FileId", ctypes.c_ubyte * 16),
    ]


class _FILE_DISPOSITION_INFO(ctypes.Structure):
    # FILE_DISPOSITION_INFO uses BOOLEAN (one byte), not Win32 BOOL.
    _fields_ = [("DeleteFile", ctypes.c_ubyte)]


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


@lru_cache(maxsize=1)
def _kernel32():
    if os.name != "nt":
        raise OSError("Clearspace cleanup requires Windows 10 or Windows 11.")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                               wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    api.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                ctypes.c_void_p, wintypes.DWORD]
    api.GetFileInformationByHandleEx.restype = wintypes.BOOL
    api.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR,
                                            wintypes.DWORD, wintypes.DWORD]
    api.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    api.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                              ctypes.c_void_p, wintypes.DWORD]
    api.SetFileInformationByHandle.restype = wintypes.BOOL
    api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    api.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    api.Process32FirstW.restype = wintypes.BOOL
    api.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    api.Process32NextW.restype = wintypes.BOOL
    return api


def _error() -> OSError:
    return ctypes.WinError(ctypes.get_last_error())


def _path_key(path: Path | str) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def supported_path(path: Path) -> Path:
    """Keep paths lexical: resolving first would hide a reparse point."""
    path = Path(path)
    if not path.is_absolute() or any(part in (".", "..") for part in path.parts):
        raise UnsafePathError("The path must be absolute and contain no traversal.")
    path = Path(os.path.abspath(path))
    if (not path.is_absolute() or not path.drive or path.drive.startswith("\\")
            or len(path.drive) != 2 or path.drive[1] != ":"):
        raise UnsafePathError("Only local drive paths are supported.")
    for part in path.parts[1:]:
        if part in (".", "..") or any(character in part for character in ':*?\x00<>|"'):
            raise UnsafePathError("The path contains unsupported components.")
        if part.endswith((".", " ")):
            raise UnsafePathError("Ambiguous Windows path names are not supported.")
    return path


@dataclass(frozen=True)
class FileIdentity:
    volume: int
    identifier: bytes


@dataclass(frozen=True)
class FileSnapshot:
    identity: FileIdentity
    size: int
    creation_ticks: int
    modified_ticks: int
    change_ticks: int
    attributes: int
    links: int
    directory: bool
    delete_pending: bool

    @property
    def modified_at(self) -> float:
        return (self.modified_ticks - WINDOWS_EPOCH_TICKS) / 10_000_000

    @property
    def created_at(self) -> float:
        return (self.creation_ticks - WINDOWS_EPOCH_TICKS) / 10_000_000

    @property
    def eligible_file(self) -> bool:
        return (not self.directory and not self.delete_pending and self.links == 1
                and not self.attributes & FILE_ATTRIBUTE_REPARSE_POINT)


class Handle:
    def __init__(self, value):
        self.value = value

    def close(self):
        if self.value is not None:
            _kernel32().CloseHandle(self.value)
            self.value = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def snapshot(self) -> FileSnapshot:
        api = _kernel32()
        basic = _FILE_BASIC_INFO()
        standard = _FILE_STANDARD_INFO()
        identifier = _FILE_ID_INFO()
        for kind, value in ((0, basic), (1, standard), (18, identifier)):
            if not api.GetFileInformationByHandleEx(self.value, kind,
                                                   ctypes.byref(value), ctypes.sizeof(value)):
                raise _error()
        return FileSnapshot(
            FileIdentity(identifier.VolumeSerialNumber, bytes(identifier.FileId)),
            standard.EndOfFile, basic.CreationTime, basic.LastWriteTime,
            basic.ChangeTime, basic.FileAttributes, standard.NumberOfLinks,
            bool(standard.Directory), bool(standard.DeletePending),
        )

    def final_path(self) -> Path:
        api = _kernel32()
        size = api.GetFinalPathNameByHandleW(self.value, None, 0, 0)
        if not size:
            raise _error()
        if size > 32768:
            raise UnsafePathError("The resolved path is too long.")
        buffer = ctypes.create_unicode_buffer(size + 1)
        actual = api.GetFinalPathNameByHandleW(self.value, buffer, len(buffer), 0)
        if not actual or actual >= len(buffer):
            raise _error()
        text = buffer.value
        if text.startswith("\\\\?\\"):
            text = text[4:]
        return supported_path(Path(text))

    def mark_for_deletion(self):
        disposition = _FILE_DISPOSITION_INFO(1)
        if not _kernel32().SetFileInformationByHandle(
                self.value, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)):
            raise _error()


def open_handle(path: Path, *, directory: bool = False, delete: bool = False) -> Handle:
    """Open a final component without following reparses; do not fall back."""
    path = supported_path(path)
    flags = FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= FILE_FLAG_BACKUP_SEMANTICS
    # Deletion is exclusive: even an existing reader makes this entry a skip.
    sharing = 0 if delete else FILE_SHARE_READ
    # Metadata-only opens do not participate fully in Windows share accounting.
    # Request data/list access too so our no-delete share mode actually pins it.
    value = _kernel32().CreateFileW(str(path), FILE_READ_DATA | FILE_READ_ATTRIBUTES | (DELETE if delete else 0),
                                   sharing, None, OPEN_EXISTING, flags, None)
    if value == INVALID_HANDLE_VALUE:
        raise _error()
    handle = Handle(value)
    try:
        snapshot = handle.snapshot()
        if snapshot.attributes & FILE_ATTRIBUTE_REPARSE_POINT:
            raise UnsafePathError("Reparse points are excluded.")
        if snapshot.directory != directory:
            raise UnsafePathError("The entry type changed.")
        if _path_key(handle.final_path()) != _path_key(path):
            raise UnsafePathError("The opened path does not match the expected location.")
        return handle
    except BaseException:
        handle.close()
        raise


@dataclass
class PinnedDirectories:
    path: Path
    handles: list[Handle]
    identities: tuple[tuple[str, FileIdentity], ...]


@contextmanager
def pin_directories(path: Path,
                    expected: tuple[tuple[str, FileIdentity], ...] | None = None
                    ) -> Iterator[PinnedDirectories]:
    """Pin every component from the drive root, rejecting identity changes."""
    path = supported_path(path)
    chain = list(reversed(path.parents)) + [path]
    handles: list[Handle] = []
    identities: list[tuple[str, FileIdentity]] = []
    expected_map = dict(expected) if expected is not None else None
    try:
        for directory in chain:
            handle = open_handle(directory, directory=True)
            handles.append(handle)
            identity = handle.snapshot().identity
            key = _path_key(directory)
            if expected_map is not None and expected_map.get(key) != identity:
                raise UnsafePathError("A parent directory changed since the scan.")
            identities.append((key, identity))
        if expected is not None and len(expected) != len(identities):
            raise UnsafePathError("The parent directory chain changed.")
        yield PinnedDirectories(path, handles, tuple(identities))
    finally:
        for handle in reversed(handles):
            handle.close()


@contextmanager
def pin_child(parent: PinnedDirectories, path: Path) -> Iterator[PinnedDirectories]:
    path = supported_path(path)
    if _path_key(path.parent) != _path_key(parent.path):
        raise UnsafePathError("The child is outside the pinned directory.")
    with open_handle(path, directory=True) as handle:
        identity = handle.snapshot().identity
        yield PinnedDirectories(path, parent.handles + [handle],
                                parent.identities + ((_path_key(path), identity),))


def local_app_data() -> Path:
    """Ask the Windows shell for the actual current-user LocalAppData folder."""
    if os.name != "nt":
        raise OSError("Clearspace requires Windows 10 or Windows 11.")
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(_GUID), wintypes.DWORD,
                                           wintypes.HANDLE, ctypes.POINTER(wintypes.LPWSTR)]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None
    folder = _GUID.from_buffer_copy(uuid.UUID("F1B32785-6FBA-4FCF-9D55-7B8E7F157091").bytes_le)
    result = wintypes.LPWSTR()
    status = shell32.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(result))
    try:
        if status < 0:
            raise OSError("The Windows LocalAppData folder is unavailable.")
        return supported_path(Path(result.value))
    finally:
        if result:
            ole32.CoTaskMemFree(ctypes.cast(result, ctypes.c_void_p))


def running_processes() -> set[str]:
    """Return executable basenames; inability to enumerate is an error."""
    api = _kernel32()
    value = api.CreateToolhelp32Snapshot(0x2, 0)
    if value == INVALID_HANDLE_VALUE:
        raise _error()
    with Handle(value) as snapshot:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not api.Process32FirstW(snapshot.value, ctypes.byref(entry)):
            raise _error()
        result: set[str] = set()
        while True:
            result.add(entry.szExeFile.casefold())
            if not api.Process32NextW(snapshot.value, ctypes.byref(entry)):
                # ERROR_NO_MORE_FILES is the only successful end condition.
                if ctypes.get_last_error() != 18:
                    raise _error()
                break
        return result
