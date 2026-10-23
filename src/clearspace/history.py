"""Narrow, per-user Windows history cleanup with snapshot revalidation.

These adapters clear metadata only: Run dialog commands, Explorer search terms,
and the RecentDocs registry list. They never delete a referenced document or
program, an entire registry key, Jump List databases, pinned items, CloudStore,
UserAssist, or tracking preferences. File-backed Recent shortcuts are handled
separately by the file cleanup engine.

Windows Search's modern ``Recent`` applications list is different. Microsoft
documents clearing it through Search permissions, not a public external clear
API. ``SEARCH_HISTORY_SETTINGS_URI`` opens that exact supported settings page;
these registry adapters deliberately do not claim to clear that list.

Each cleanup requires the exact snapshot issued by its provider. Keys are opened
and checked before any deletion; each remaining value set is checked again
before a value is deleted. New or changed history is kept. Registry APIs offer
no conditional delete or writer lock, so a concurrent writer can still race an
individual comparison. Only allowlisted history metadata can be affected;
referenced user files, pinned lists, preferences, and whole keys are preserved.

References:
https://learn.microsoft.com/en-us/windows/win32/sysinfo/writing-and-deleting-registry-data
https://learn.microsoft.com/en-us/windows/apps/develop/launch/launch-settings
https://support.microsoft.com/en-gb/windows/privacy/windows-search-and-privacy
https://github.com/bleachbit/bleachbit/blob/master/cleaners/windows_explorer.xml
https://github.com/MoscaDotTo/Winapp2/blob/master/Winapp2.ini
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import os
from threading import Lock
from typing import Iterator


SEARCH_HISTORY_SETTINGS_URI = "ms-settings:search-permissions"
_EXPLORER = r"Software\Microsoft\Windows\CurrentVersion\Explorer"
_REG_SZ = 1
_REG_BINARY = 3
_MAX_KEYS = 512
_MAX_VALUES = 10_000
_MAX_VALUE_BYTES = 1_048_576
_MAX_TOTAL_BYTES = 8_388_608
_REG_OPTION_OPEN_LINK = 0x00000008


class HistoryError(OSError):
    """History could not be analyzed or safely cleared."""


class HistoryChangedError(HistoryError):
    """Windows changed the analyzed history; analysis must run again."""


@dataclass(frozen=True)
class HistorySpec:
    id: str
    title: str
    path: str
    children: bool = False


HISTORY_SPECS = (
    HistorySpec("run_history", "Run dialog history", _EXPLORER + r"\RunMRU"),
    HistorySpec("explorer_search_history", "File Explorer search history",
                _EXPLORER + r"\WordWheelQuery"),
    HistorySpec("recent_document_history", "Recent document history",
                _EXPLORER + r"\RecentDocs", children=True),
)
_SPECS = {spec.id: spec for spec in HISTORY_SPECS}


@dataclass(frozen=True)
class _Value:
    name: str
    kind: int
    data: str | bytes | int | tuple[str, ...] | None


@dataclass(frozen=True)
class _Key:
    path: str
    revision: int
    values: tuple[_Value, ...]
    children: tuple[str, ...]


@dataclass(frozen=True)
class HistorySnapshot:
    category_id: str
    keys: tuple[_Key, ...]
    items: int
    bytes: int = 0
    detail: str | None = None

    @property
    def pending(self) -> bool:
        return any(_selected(self.category_id, value)
                   for key in self.keys for value in key.values)


@dataclass(frozen=True)
class HistoryResult:
    completed: bool
    freed_bytes: int | None = 0
    detail: str | None = None
    removed_items: int = 0


def _selected(category_id: str, value: _Value) -> bool:
    # Preserve default values, unknown data, and settings that happen to share a
    # history key. Index values are cleared together with their history entries.
    if category_id == "run_history":
        return value.kind == _REG_SZ and (
            value.name == "MRUList"
            or (len(value.name) == 1 and "a" <= value.name <= "z"))
    return value.kind == _REG_BINARY and (
        value.name == "MRUListEx" or value.name.isascii() and value.name.isdecimal())


def _history_item(category_id: str, value: _Value) -> bool:
    return _selected(category_id, value) and value.name not in {"MRUList", "MRUListEx"}


def _cancelled(cancel) -> bool:
    return cancel is not None and cancel.is_set()


def _freeze(data):
    if isinstance(data, list) and all(isinstance(part, str) for part in data):
        data = tuple(data)
    if (data is not None and not isinstance(data, (str, bytes, int, tuple))
            or isinstance(data, tuple) and not all(isinstance(part, str) for part in data)):
        raise HistoryError("Windows returned unsupported history metadata.")
    size = len(data) if isinstance(data, (str, bytes, tuple)) else 8
    if size > _MAX_VALUE_BYTES:
        raise HistoryError("A history value exceeds the supported analysis limit.")
    return data


def _metadata_bytes(values: tuple[_Value, ...] | list[_Value]) -> int:
    total = 0
    for value in values:
        total += len(value.name.encode("utf-16-le"))
        if isinstance(value.data, str):
            total += len(value.data.encode("utf-16-le"))
        elif isinstance(value.data, bytes):
            total += len(value.data)
        elif isinstance(value.data, tuple):
            total += sum(len(part.encode("utf-16-le")) for part in value.data)
        else:
            total += 8
    return total


class _WindowsRegistry:
    """Private backend. Production paths come exclusively from HISTORY_SPECS."""

    def __init__(self):
        if os.name != "nt":
            raise HistoryError("Windows history cleanup requires Windows 10 or Windows 11.")
        import winreg
        self.registry = winreg

    @contextmanager
    def _open(self, path: str, *, writable: bool = False) -> Iterator:
        reg = self.registry
        with ExitStack() as stack:
            parent = reg.HKEY_CURRENT_USER
            parts = path.split("\\")
            for index, part in enumerate(parts):
                access = reg.KEY_READ | reg.KEY_WOW64_64KEY
                if writable and index == len(parts) - 1:
                    access |= reg.KEY_SET_VALUE
                key = stack.enter_context(reg.OpenKey(parent, part, _REG_OPTION_OPEN_LINK, access))
                try:
                    _, kind = reg.QueryValueEx(key, "SymbolicLinkValue")
                except FileNotFoundError:
                    pass
                else:
                    if kind == reg.REG_LINK:
                        raise HistoryError("A history path contains an unsupported registry link.")
                parent = key
            yield parent

    def _read_open_key(self, key, path: str) -> _Key:
        reg = self.registry
        before = reg.QueryInfoKey(key)
        subkeys, count, revision = before
        if subkeys > _MAX_KEYS or count > _MAX_VALUES:
            raise HistoryError("Windows history exceeds the supported analysis limit.")
        children = tuple(sorted((reg.EnumKey(key, index) for index in range(subkeys)),
                                key=str.casefold))
        values = []
        metadata_bytes = 0
        for index in range(count):
            name, data, kind = reg.EnumValue(key, index)
            if kind == reg.REG_LINK:
                raise HistoryError("A history key contains an unsupported registry link.")
            value = _Value(name, kind, _freeze(data))
            metadata_bytes += _metadata_bytes([value])
            if metadata_bytes > _MAX_TOTAL_BYTES:
                raise HistoryError("Windows history exceeds the supported analysis limit.")
            values.append(value)
        if reg.QueryInfoKey(key) != before:
            raise HistoryChangedError("History changed during analysis. Analyze again.")
        return _Key(path, revision, tuple(sorted(values, key=lambda item: item.name.casefold())),
                    children)

    def _read_key(self, path: str) -> _Key:
        with self._open(path) as key:
            return self._read_open_key(key, path)

    def read(self, spec: HistorySpec) -> tuple[_Key, ...]:
        try:
            root = self._read_key(spec.path)
        except FileNotFoundError:
            return ()
        keys = [root]
        if spec.children:
            for child in root.children:
                if not child or any(character in child for character in "\\/\x00"):
                    raise HistoryError("Windows returned an unsupported history key.")
                keys.append(self._read_key(spec.path + "\\" + child))
                if (sum(len(key.values) for key in keys) > _MAX_VALUES
                        or sum(_metadata_bytes(key.values) for key in keys) > _MAX_TOTAL_BYTES):
                    raise HistoryError("Windows history exceeds the supported analysis limit.")
        return tuple(keys)

    def clear(self, spec: HistorySpec, snapshot: HistorySnapshot, cancel=None) -> HistoryResult:
        removed = 0
        changed = False
        with ExitStack() as stack:
            handles = {}
            # Keep every target key handle open until cleanup finishes. Do not
            # resolve a key path again after it has passed the snapshot check.
            for scanned in snapshot.keys:
                if _cancelled(cancel):
                    return HistoryResult(False, detail="History cleanup was cancelled.")
                try:
                    key = stack.enter_context(self._open(scanned.path, writable=True))
                except FileNotFoundError as error:
                    raise HistoryChangedError("History changed since analysis. Analyze again.") from error
                if self._read_open_key(key, scanned.path) != scanned:
                    raise HistoryChangedError("History changed since analysis. Analyze again.")
                handles[scanned.path] = key
            for scanned in snapshot.keys:
                key = handles[scanned.path]
                remaining = list(scanned.values)
                selected = [value for value in scanned.values if _selected(spec.id, value)]
                # Deleting an MRU order/index before its entries could leave
                # stale order data if cancellation or a concurrent edit occurs.
                selected.sort(key=lambda value: value.name in {"MRUList", "MRUListEx"})
                for value in selected:
                    if _cancelled(cancel):
                        return HistoryResult(False, detail="History cleanup stopped; remaining entries were kept.",
                                             removed_items=removed)
                    try:
                        current = self._read_open_key(key, scanned.path)
                        if current.values != tuple(remaining) or current.children != scanned.children:
                            changed = True
                            break
                        self.registry.DeleteValue(key, value.name)
                    except OSError as error:
                        # A deletion may race a write despite the comparison.
                        # Keep the rest and report partial completion honestly.
                        if isinstance(error, HistoryError):
                            detail = str(error)
                        else:
                            detail = "Windows could not clear every history entry; remaining entries were kept."
                        return HistoryResult(False, detail=detail, removed_items=removed)
                    remaining.remove(value)
                    removed += _history_item(spec.id, value)
        if changed:
            return HistoryResult(False, detail="Some history changed during cleanup and was kept. Analyze again.",
                                 removed_items=removed)
        return HistoryResult(True, detail="History cleared. Windows may refresh the list after you sign in again.",
                             removed_items=removed)


class WindowsHistory:
    """A single allowlisted HKCU history; no public path or command parameter."""

    def __init__(self, category_id: str, *, backend=None):
        if category_id not in _SPECS:
            raise ValueError("Unknown Windows history category.")
        self.spec = _SPECS[category_id]
        self._backend = backend
        self._snapshot: HistorySnapshot | None = None
        self._lock = Lock()

    def _registry(self):
        if self._backend is None:
            self._backend = _WindowsRegistry()
        return self._backend

    def query(self) -> HistorySnapshot:
        with self._lock:
            self._snapshot = None
            try:
                keys = self._registry().read(self.spec)
            except HistoryError:
                raise
            except OSError as error:
                raise HistoryError("Windows could not read this history.") from error
            count = sum(_history_item(self.spec.id, value) for key in keys for value in key.values)
            detail = (f"{count} history {'entry' if count == 1 else 'entries'}. "
                      "Referenced files and pinned items are kept.")
            snapshot = HistorySnapshot(self.spec.id, keys, count, detail=detail)
            self._snapshot = snapshot
            return snapshot

    def clean(self, snapshot: HistorySnapshot, cancel=None) -> HistoryResult:
        with self._lock:
            if (not isinstance(snapshot, HistorySnapshot) or snapshot is not self._snapshot
                    or snapshot.category_id != self.spec.id):
                raise HistoryError("This history analysis is no longer valid. Analyze again.")
            self._snapshot = None
            if _cancelled(cancel):
                return HistoryResult(False, detail="History cleanup was cancelled.")
            if not snapshot.pending:
                return HistoryResult(True, detail="This history is already empty.")
            try:
                return self._registry().clear(self.spec, snapshot, cancel)
            except HistoryError:
                raise
            except OSError as error:
                raise HistoryError("Windows could not clear this history. Analyze again.") from error


def history_providers() -> dict[str, WindowsHistory]:
    return {spec.id: WindowsHistory(spec.id) for spec in HISTORY_SPECS}
