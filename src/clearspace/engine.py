"""Snapshot-based cleanup of explicitly supported Windows cache locations.

The production engine is Windows-only and never removes directories. A scan
does not modify files. Cleanup accepts the exact in-memory plan created by this
Cleaner, revalidates its directory/file identities and uses handle deletion.
The file-size total is logical bytes removed, not a measurement of free disk
space (compression, sparse files and concurrent disk activity affect that).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
import os
from pathlib import Path
import re
import stat
import threading
import time
from typing import Callable, Iterable
import uuid

from . import windows


DAY = 24 * 60 * 60
PLAN_LIFETIME = 30 * 60
MAX_FILES = 50_000
MAX_VISITED = 200_000
MAX_DEPTH = 64
MAX_SCAN_SECONDS = 120
MAX_ISSUES = 200


@dataclass(frozen=True)
class Category:
    id: str
    label: str
    description: str
    roots: tuple[Path, ...]
    default_selected: bool = False
    processes: tuple[str, ...] = ()
    minimum_age_days: int = 0
    reason: str | None = None
    file_patterns: tuple[str, ...] = ()
    exclude_patterns: tuple[str, ...] = ()
    group: str = "Windows"
    recursive: bool = True

    @property
    def available(self) -> bool:
        return bool(self.roots) and self.reason is None

    def to_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label, "description": self.description,
            "default_selected": self.default_selected, "available": self.available,
            "reason": self.reason, "minimum_age_days": self.minimum_age_days,
            "locations": [str(path) for path in self.roots],
            "file_patterns": list(self.file_patterns),
            "exclude_patterns": list(self.exclude_patterns), "group": self.group,
            "recursive": self.recursive,
        }


@dataclass(frozen=True)
class Entry:
    id: str
    category_id: str
    path: Path
    size: int
    modified_at: float
    _snapshot: windows.FileSnapshot = field(repr=False)
    _ancestors: tuple[tuple[str, windows.FileIdentity], ...] = field(repr=False)
    _minimum_age_days: int = field(repr=False)

    def to_dict(self) -> dict:
        return {"id": self.id, "category_id": self.category_id, "path": str(self.path),
                "name": self.path.name, "size": self.size, "modified_at": self.modified_at}


@dataclass(frozen=True)
class CategoryStats:
    category_id: str
    files: int = 0
    bytes: int = 0
    skipped: int = 0
    reason: str | None = None

    def to_dict(self) -> dict:
        return {"category_id": self.category_id, "files": self.files,
                "bytes": self.bytes, "skipped": self.skipped, "reason": self.reason}


@dataclass(frozen=True)
class Plan:
    id: str
    created_at: float
    expires_at: float
    entries: tuple[Entry, ...]
    category_stats: tuple[CategoryStats, ...]
    cancelled: bool
    truncated: bool
    skipped_files: int

    @property
    def total_files(self) -> int:
        return len(self.entries)

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.entries)

    def to_dict(self, include_entries: bool = False) -> dict:
        result = {
            "id": self.id, "created_at": self.created_at, "expires_at": self.expires_at,
            "categories": [item.to_dict() for item in self.category_stats],
            "total_files": self.total_files, "total_bytes": self.total_bytes,
            "skipped_files": self.skipped_files, "cancelled": self.cancelled,
            "truncated": self.truncated,
        }
        if include_entries:
            result["entries"] = [item.to_dict() for item in self.entries]
        return result


@dataclass(frozen=True)
class Result:
    deleted_files: int
    freed_bytes: int
    skipped_files: int
    failed_files: int
    cancelled: bool
    by_category: dict[str, dict[str, int]]
    issues: tuple[dict[str, str], ...]

    def to_dict(self) -> dict:
        return {
            "deleted_files": self.deleted_files, "freed_bytes": self.freed_bytes,
            "skipped_files": self.skipped_files, "failed_files": self.failed_files,
            "cancelled": self.cancelled,
            "by_category": {key: dict(value) for key, value in self.by_category.items()},
            "issues": [dict(issue) for issue in self.issues],
        }


def _existing_directory(path: Path) -> bool:
    try:
        with windows.pin_directories(path):
            return True
    except OSError:
        return False


def _profiles(base: Path, pattern: str) -> tuple[Path, ...]:
    try:
        with windows.pin_directories(base):
            with os.scandir(base) as children:
                paths = [Path(child.path) for child in children
                         if re.fullmatch(pattern, child.name) and _existing_directory(Path(child.path))]
        return tuple(sorted(paths, key=lambda path: path.name.casefold()))
    except OSError:
        return ()


def discover_categories() -> list[Category]:
    """Discover fixed supported folders; never take a path from renderer input."""
    from .catalog import discover_categories as discover
    return discover()


def _cancelled(cancel) -> bool:
    return cancel is not None and cancel.is_set()


def _report(progress, value: dict):
    if progress is not None:
        try:
            progress(value)
        except Exception:
            # UI/report delivery must not interrupt accounting after a deletion.
            pass


class Cleaner:
    """The optional categories/process provider are internal test dependencies.

    Production callers construct Cleaner() and receive only known Windows
    folders. A Plan is not serializable as deletion authority: the exact latest
    plan object must belong to this Cleaner and is consumed by the first clean.
    """

    def __init__(self, categories: Iterable[Category] | None = None,
                 process_provider: Callable[[], set[str]] | None = None, *,
                 max_files: int = MAX_FILES, max_visited: int = MAX_VISITED,
                 max_depth: int = MAX_DEPTH, max_scan_seconds: float = MAX_SCAN_SECONDS):
        self.categories = tuple(discover_categories() if categories is None else categories)
        if len({item.id for item in self.categories}) != len(self.categories):
            raise ValueError("Category IDs must be unique.")
        for item in self.categories:
            if not item.id or type(item.minimum_age_days) is not int or item.minimum_age_days < 0:
                raise ValueError("Cleaning rule age limits must be nonnegative whole days.")
            if type(item.recursive) is not bool:
                raise ValueError("Cleaning rule recursion must be enabled or disabled explicitly.")
            for patterns in (item.file_patterns, item.exclude_patterns):
                if not isinstance(patterns, tuple) or any(
                        not isinstance(pattern, str) or not pattern
                        or any(character in pattern for character in '/\\:\x00') for pattern in patterns):
                    raise ValueError("Cleaning rules must use immutable file-name patterns.")
            for root in item.roots:
                windows.supported_path(root)
        if max_files < 1 or max_visited < 1 or max_depth < 1 or max_scan_seconds <= 0:
            raise ValueError("Scan limits must be positive.")
        self._process_provider = process_provider or windows.running_processes
        self._max_files = min(max_files, MAX_FILES)
        self._max_visited = min(max_visited, MAX_VISITED)
        self._max_depth = min(max_depth, MAX_DEPTH)
        self._max_scan_seconds = min(max_scan_seconds, MAX_SCAN_SECONDS)
        self._operation = threading.Lock()
        self._latest_plan: Plan | None = None

    def _selection(self, ids: Iterable[str]) -> tuple[Category, ...]:
        if isinstance(ids, (str, bytes)):
            raise ValueError("Choose categories from the available list.")
        selected = tuple(ids)
        if not selected or any(not isinstance(value, str) for value in selected):
            raise ValueError("Choose at least one category.")
        known = {item.id: item for item in self.categories}
        if any(value not in known for value in selected):
            raise ValueError("One of the selected categories is unavailable.")
        return tuple(known[value] for value in dict.fromkeys(selected))

    def _process_reason(self, category: Category) -> str | None:
        if not category.processes:
            return None
        try:
            processes = self._process_provider()
            if not isinstance(processes, (set, frozenset)) or any(not isinstance(item, str) for item in processes):
                raise ValueError("Invalid process snapshot")
            processes = {name.casefold() for name in processes}
        except Exception:
            return "Could not check running applications. This category was skipped."
        if processes.intersection(name.casefold() for name in category.processes):
            return f"Close {category.label} and analyze again."
        return None

    def scan(self, category_ids: Iterable[str], cancel=None, progress=None) -> Plan:
        categories = self._selection(category_ids)
        if not self._operation.acquire(blocking=False):
            raise RuntimeError("Another cleanup operation is already running.")
        try:
            self._latest_plan = None
            created = time.time()
            started = time.monotonic()
            entries: list[Entry] = []
            stats: list[CategoryStats] = []
            visited = 0
            total_bytes = 0
            truncated = False
            seen_files: set[windows.FileIdentity] = set()
            seen_directories: set[windows.FileIdentity] = set()
            last_report = 0.0

            def report(category_id, force=False):
                nonlocal last_report
                now = time.monotonic()
                if force or now - last_report >= 0.1:
                    last_report = now
                    _report(progress, {"phase": "scanning", "category_id": category_id,
                                       "visited_files": visited, "found_files": len(entries),
                                       "found_bytes": total_bytes, "processed_files": 0, "total_files": 0})

            def stop() -> bool:
                nonlocal truncated
                if _cancelled(cancel):
                    return True
                if (len(entries) >= self._max_files or visited >= self._max_visited
                        or time.monotonic() - started >= self._max_scan_seconds):
                    truncated = True
                    return True
                return False

            for category in categories:
                current_files = 0
                current_bytes = 0
                skipped = 0
                reason = category.reason if not category.available else self._process_reason(category)
                if stop():
                    reason = "Analysis stopped before this category was completed."
                if reason:
                    stats.append(CategoryStats(category.id, reason=reason))
                    report(category.id, True)
                    continue

                def walk(pinned: windows.PinnedDirectories, depth: int):
                    nonlocal visited, current_files, current_bytes, total_bytes, skipped, truncated
                    if depth > self._max_depth:
                        truncated = True
                        skipped += 1
                        return
                    directory_id = pinned.identities[-1][1]
                    if directory_id in seen_directories:
                        return
                    seen_directories.add(directory_id)
                    try:
                        with os.scandir(pinned.path) as children:
                            for child in children:
                                if stop():
                                    break
                                visited += 1
                                path = Path(child.path)
                                try:
                                    value = child.stat(follow_symlinks=False)
                                    if (getattr(value, "st_file_attributes", 0) & windows.FILE_ATTRIBUTE_REPARSE_POINT
                                            or stat.S_ISLNK(value.st_mode)):
                                        skipped += 1
                                    elif stat.S_ISDIR(value.st_mode):
                                        if category.recursive:
                                            with windows.pin_child(pinned, path) as descendant:
                                                walk(descendant, depth + 1)
                                        else:
                                            skipped += 1
                                    elif stat.S_ISREG(value.st_mode):
                                        name = child.name.casefold()
                                        if ((category.file_patterns and not any(
                                                fnmatchcase(name, pattern.casefold()) for pattern in category.file_patterns))
                                                or any(fnmatchcase(name, pattern.casefold())
                                                       for pattern in category.exclude_patterns)):
                                            skipped += 1
                                            report(category.id)
                                            continue
                                        # Metadata reads allow the owning app to continue operating.
                                        with windows.open_handle(path) as handle:
                                            snapshot = handle.snapshot()
                                        if (not snapshot.eligible_file or snapshot.size < 0
                                                or (category.minimum_age_days > 0 and
                                                    max(snapshot.created_at, snapshot.modified_at) > created - category.minimum_age_days * DAY)):
                                            skipped += 1
                                        elif snapshot.identity not in seen_files:
                                            # Opening DELETE access is a read-only capability probe:
                                            # never set disposition during analysis. This excludes
                                            # files already in use or lacking removal permission.
                                            with windows.open_handle(path, delete=True) as probe:
                                                stable = probe.snapshot()
                                                if stable != snapshot:
                                                    skipped += 1
                                                    report(category.id)
                                                    continue
                                                seen_files.add(snapshot.identity)
                                                entry = Entry(uuid.uuid4().hex, category.id, path, snapshot.size,
                                                              snapshot.modified_at, snapshot, pinned.identities,
                                                              category.minimum_age_days)
                                                entries.append(entry)
                                                current_files += 1
                                                current_bytes += snapshot.size
                                                total_bytes += snapshot.size
                                    else:
                                        skipped += 1
                                except (OSError, ValueError):
                                    skipped += 1
                                report(category.id)
                    except OSError:
                        skipped += 1

                for root in category.roots:
                    if stop():
                        break
                    try:
                        with windows.pin_directories(root) as pinned:
                            walk(pinned, 0)
                    except (OSError, ValueError):
                        skipped += 1
                        reason = "Some locations were unavailable or changed during analysis."
                if truncated:
                    reason = "Analysis limit reached. These results contain only the files already checked."
                elif _cancelled(cancel):
                    reason = "Analysis cancelled. Run Analyze again before cleaning."
                stats.append(CategoryStats(category.id, current_files, current_bytes, skipped, reason))
                report(category.id, True)
            plan = Plan(uuid.uuid4().hex, created, created + PLAN_LIFETIME, tuple(entries),
                        tuple(stats), _cancelled(cancel), truncated, sum(item.skipped for item in stats))
            self._latest_plan = plan
            report(None, True)
            return plan
        finally:
            self._operation.release()

    def clean(self, plan: Plan, category_ids: Iterable[str], cancel=None, progress=None) -> Result:
        categories = self._selection(category_ids)
        if not self._operation.acquire(blocking=False):
            raise RuntimeError("Another cleanup operation is already running.")
        try:
            if not isinstance(plan, Plan) or self._latest_plan is not plan:
                raise ValueError("This analysis is no longer valid. Run Analyze again.")
            if time.time() > plan.expires_at:
                self._latest_plan = None
                raise ValueError("This analysis expired. Run Analyze again.")
            if plan.cancelled or plan.truncated:
                raise ValueError("The analysis did not finish. Run Analyze again before cleaning.")
            scanned_ids = {item.category_id for item in plan.category_stats}
            if any(category.id not in scanned_ids for category in categories):
                raise ValueError("Analyze the selected categories before cleaning.")
            # Consume before starting. A second click cannot replay this snapshot.
            self._latest_plan = None
            selected = {category.id: category for category in categories}
            entries = tuple(entry for entry in plan.entries if entry.category_id in selected)
            outcomes = {category.id: {"deleted_files": 0, "freed_bytes": 0,
                                      "skipped_files": 0, "failed_files": 0}
                        for category in categories}
            issues: list[dict[str, str]] = []
            processed = 0
            last_report = 0.0

            def issue(entry, text, failed=False):
                outcomes[entry.category_id]["failed_files" if failed else "skipped_files"] += 1
                if len(issues) < MAX_ISSUES:
                    issues.append({"path": str(entry.path), "reason": text})

            def report(category_id, force=False):
                nonlocal last_report
                now = time.monotonic()
                if force or now - last_report >= 0.1:
                    last_report = now
                    _report(progress, {"phase": "cleaning", "category_id": category_id,
                                       "visited_files": 0, "found_files": plan.total_files,
                                       "found_bytes": plan.total_bytes, "processed_files": processed,
                                       "total_files": len(entries)})

            report(None, True)
            for index, entry in enumerate(entries):
                if _cancelled(cancel):
                    for remaining in entries[index:]:
                        outcomes[remaining.category_id]["skipped_files"] += 1
                    break
                category = selected[entry.category_id]
                # Recheck each browser entry: a browser may open after analysis.
                reason = self._process_reason(category)
                if reason:
                    issue(entry, reason)
                    processed += 1
                    report(entry.category_id)
                    continue
                try:
                    with windows.pin_directories(entry.path.parent, entry._ancestors):
                        with windows.open_handle(entry.path, delete=True) as handle:
                            snapshot = handle.snapshot()
                            if (snapshot != entry._snapshot or not snapshot.eligible_file
                                    or (entry._minimum_age_days > 0 and
                                        max(snapshot.created_at, snapshot.modified_at) > time.time() - entry._minimum_age_days * DAY)):
                                issue(entry, "File changed since analysis; kept.")
                            elif _cancelled(cancel):
                                issue(entry, "Cleanup cancelled; kept.")
                            else:
                                handle.mark_for_deletion()
                                outcomes[entry.category_id]["deleted_files"] += 1
                                outcomes[entry.category_id]["freed_bytes"] += entry.size
                except windows.UnsafePathError:
                    issue(entry, "Location changed or contains a link; kept.")
                except OSError as error:
                    if getattr(error, "winerror", None) in (2, 3, 5, 32, 33, 303):
                        issue(entry, "File is in use, unavailable or no longer accessible; kept.")
                    else:
                        issue(entry, "Windows could not remove this file; kept.", failed=True)
                processed += 1
                report(entry.category_id)
            result = Result(
                sum(item["deleted_files"] for item in outcomes.values()),
                sum(item["freed_bytes"] for item in outcomes.values()),
                sum(item["skipped_files"] for item in outcomes.values()),
                sum(item["failed_files"] for item in outcomes.values()),
                _cancelled(cancel), outcomes, tuple(issues),
            )
            report(None, True)
            return result
        finally:
            self._operation.release()
