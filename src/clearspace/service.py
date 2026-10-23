"""Serialized desktop state; the renderer never supplies filesystem paths."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import threading

from . import __version__
from .engine import Cleaner
from .windows import local_app_data


class ServiceError(ValueError):
    """A bounded message suitable for display in the local interface."""


class CleanerService:
    def __init__(self, cleaner=None, data_dir=None):
        self._cleaner = cleaner if cleaner is not None else Cleaner()
        self._data_dir = Path(data_dir) if data_dir is not None else local_app_data() / "Clearspace"
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._worker = None
        self._closed = False
        self._plan = None
        self._categories = {category.id: category for category in self._cleaner.categories}
        defaults = [item.id for item in self._categories.values() if item.default_selected and item.available]
        self._selected = self._read_selection(defaults)
        self._state = {
            "version": __version__, "phase": "idle", "categories": [], "scan_id": None,
            "scanned_at": None, "progress": self._empty_progress(), "result": None, "error": None,
        }
        self._refresh_categories()

    @staticmethod
    def _empty_progress():
        return {"checked": 0, "files": 0, "bytes": 0, "current": None}

    def _read_selection(self, defaults):
        try:
            path = self._data_dir / "settings.json"
            if path.stat().st_size > 65536:
                return set(defaults)
            value = json.loads(path.read_text(encoding="utf-8"))
            values = value.get("selected") if isinstance(value, dict) else None
            if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                return set(defaults)
            selected = {item for item in values if item in self._categories and self._categories[item].available}
            if value.get("version", 1) == 1:
                # Preserve existing choices while introducing the new recommended
                # Windows cleanup categories to users of the five-category MVP.
                legacy = {"user_temp", "chrome_cache", "edge_cache", "firefox_cache", "pip_cache"}
                selected.update(set(defaults) - legacy)
            return selected
        except (OSError, ValueError):
            return set(defaults)

    def _write_json(self, name, value):
        self._data_dir.mkdir(parents=True, exist_ok=True)
        temporary = self._data_dir / f".{name}.{secrets.token_hex(8)}.tmp"
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as output:
                json.dump(value, output, ensure_ascii=True, indent=2, allow_nan=False)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(self._data_dir / name)
        finally:
            temporary.unlink(missing_ok=True)

    def _refresh_categories(self):
        stats = {item.category_id: item for item in self._plan.category_stats} if self._plan else {}
        rows = []
        for category in self._categories.values():
            found = stats.get(category.id)
            rows.append({
                "id": category.id, "title": category.label, "description": category.description,
                "selected": category.id in self._selected, "available": category.available,
                "group": getattr(category, "group", "Windows"), "recommended": category.default_selected,
                "native_action": getattr(category, "native_action", None),
                "reason": (found.reason if found and found.reason else category.reason),
                "files": found.files if found else 0, "bytes": found.bytes if found else 0,
                "skipped": found.skipped if found else 0,
                "actions": getattr(found, "actions", 0) if found else 0,
                "size_known": getattr(found, "size_known", True) if found else True,
                "analysis_error": getattr(found, "analysis_error", False) if found else False,
            })
        self._state["categories"] = rows

    def get_state(self):
        with self._lock:
            return deepcopy(self._state)

    def _require_idle(self):
        if self._closed:
            raise ServiceError("Clearspace is closing.")
        if self._state["phase"] in {"scanning", "cleaning"}:
            raise ServiceError("Wait for the current operation or cancel it first.")

    def _validate_ids(self, ids):
        if not isinstance(ids, list) or len(ids) > len(self._categories) or any(not isinstance(item, str) for item in ids):
            raise ServiceError("Choose supported cleanup categories.")
        if len(ids) != len(set(ids)) or any(item not in self._categories for item in ids):
            raise ServiceError("Choose supported cleanup categories.")
        if any(not self._categories[item].available for item in ids):
            raise ServiceError("One of these categories is unavailable on this computer.")
        return set(ids)

    def set_selection(self, ids):
        with self._lock:
            self._require_idle()
            selected = self._validate_ids(ids)
            if selected == self._selected:
                return self.get_state()
            self._selected = selected
            self._plan = None
            self._state.update(phase="idle", scan_id=None, scanned_at=None, progress=self._empty_progress(), result=None, error=None)
            self._refresh_categories()
            try:
                self._write_json("settings.json", {"version": 2, "selected": sorted(selected)})
            except OSError:
                self._state["error"] = "Your selection is ready, but preferences could not be saved."
            return self.get_state()

    def _progress(self, value):
        with self._lock:
            cleaning = value.get("phase") == "cleaning"
            self._state["progress"] = {
                "checked": max(0, int(value.get("processed_files" if cleaning else "visited_files", 0))),
                "files": max(0, int(value.get("total_files" if cleaning else "found_files", 0))),
                "bytes": max(0, int(value.get("found_bytes", 0))),
                "current": value.get("category_id"),
            }

    def scan(self):
        with self._lock:
            self._require_idle()
            if not self._selected:
                raise ServiceError("Select at least one category to analyze.")
            selected = sorted(self._selected)
            self._cancel = threading.Event()
            self._plan = None
            self._state.update(phase="scanning", scan_id=None, scanned_at=None, result=None, error=None, progress=self._empty_progress())
            self._refresh_categories()

            def run():
                try:
                    plan = self._cleaner.scan(selected, cancel=self._cancel, progress=self._progress)
                    with self._lock:
                        self._plan = plan
                        # A partial or cancelled analysis can be reviewed, but cannot authorize deletion.
                        usable = not plan.cancelled and not plan.truncated
                        self._state.update(
                            phase="ready" if usable else "cancelled",
                            scan_id=plan.id if usable else None,
                            scanned_at=datetime.fromtimestamp(plan.created_at, timezone.utc).isoformat(),
                            error="The analysis reached its limit. No cleanup is available from this partial result." if plan.truncated else None,
                        )
                        self._refresh_categories()
                except Exception as error:
                    self._operation_failed(error)

            self._worker = threading.Thread(target=run, name="clearspace-analysis", daemon=True)
            self._worker.start()
            return self.get_state()

    def clean(self, scan_id, ids):
        with self._lock:
            self._require_idle()
            selected = self._validate_ids(ids)
            if not isinstance(scan_id, str) or not scan_id or self._state["phase"] != "ready" or self._plan is None or scan_id != self._state["scan_id"]:
                raise ServiceError("Analyze the selected categories again before cleaning.")
            if selected != self._selected or not selected:
                raise ServiceError("Your selection changed. Analyze it again before cleaning.")
            if not any(item.files or getattr(item, "actions", 0) for item in self._plan.category_stats if item.category_id in selected):
                raise ServiceError("No selected category is ready to clean. Review its analysis and try again.")
            plan = self._plan
            self._cancel = threading.Event()
            self._state.update(phase="cleaning", scan_id=None, result=None, error=None, progress=self._empty_progress())

            def run():
                try:
                    result = self._cleaner.clean(plan, sorted(selected), cancel=self._cancel, progress=self._progress)
                    summary = {"removed": result.deleted_files, "freed_bytes": result.freed_bytes,
                               "skipped": result.skipped_files, "failed": result.failed_files, "cancelled": result.cancelled}
                    action_results = [dict(item) for item in getattr(result, "action_results", ())]
                    summary.update(actions=action_results,
                                   actions_completed=sum(item["status"] == "completed" for item in action_results),
                                   actions_failed=sum(item["status"] == "failed" for item in action_results))
                    with self._lock:
                        bin_notes = [item["reason"] for item in getattr(result, "issues", ()) if item["path"] == "Recycle Bin"]
                        self._state.update(phase="cancelled" if result.cancelled else "completed", result=summary,
                                           error=" ".join(bin_notes) or None)
                        try:
                            self._write_json("last-cleanup.json", {"version": 1, "completed_at": datetime.now(timezone.utc).isoformat(), "categories": sorted(selected), "result": summary})
                        except OSError:
                            self._state["error"] = "Cleanup finished, but its summary could not be saved."
                except Exception as error:
                    self._operation_failed(error)

            self._worker = threading.Thread(target=run, name="clearspace-cleanup", daemon=True)
            self._worker.start()
            return self.get_state()

    def _operation_failed(self, error):
        with self._lock:
            message = str(error) if isinstance(error, ValueError) else "The operation could not finish. Analyze again to retry."
            self._state.update(phase="error", scan_id=None, error=" ".join(message.split())[:500])

    def cancel(self):
        with self._lock:
            self._cancel.set()
            return self.get_state()

    def open_search_settings(self):
        with self._lock:
            self._require_idle()
            try:
                from .history import SEARCH_HISTORY_SETTINGS_URI
                os.startfile(SEARCH_HISTORY_SETTINGS_URI)
            except OSError as error:
                raise ServiceError("Windows Search settings could not be opened.") from error
            return self.get_state()

    def get_files(self, scan_id, category_id, offset=0):
        with self._lock:
            if (not isinstance(scan_id, str) or self._plan is None or scan_id != self._plan.id
                    or category_id not in self._categories or type(offset) is not int or offset < 0):
                raise ServiceError("This file list is no longer available. Analyze again.")
            if getattr(self._categories[category_id], "native_action", None):
                raise ServiceError("This Windows-managed cleanup does not provide a file preview.")
            entries = [entry for entry in self._plan.entries if entry.category_id == category_id]
            if offset > len(entries):
                raise ServiceError("This page is no longer available.")
            items = [{"name": entry.path.name, "path": str(entry.path), "bytes": entry.size} for entry in entries[offset:offset + 100]]
            next_offset = offset + len(items)
            return {"items": items, "total": len(entries), "next_offset": next_offset if next_offset < len(entries) else None}

    def active(self):
        with self._lock:
            return self._state["phase"] in {"scanning", "cleaning"}

    def shutdown(self):
        with self._lock:
            self._closed = True
            self._cancel.set()
            worker = self._worker
        if worker is not None:
            worker.join(timeout=5)
