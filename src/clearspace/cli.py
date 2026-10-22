"""Terminal access to the same reviewed cleanup engine."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading

from . import __version__


def _size(value):
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024


def main(argv=None):
    parser = argparse.ArgumentParser(prog="clearspace", description="Analyze supported Windows temporary files before removing them.")
    parser.add_argument("--version", action="version", version=f"Clearspace {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    for name in ("scan", "clean"):
        command = subparsers.add_parser(name, help="Analyze without deleting files" if name == "scan" else "Analyze, review, and explicitly confirm cleanup")
        command.add_argument("--categories", nargs="+", default=["user_temp"], metavar="ID", help="user_temp chrome_cache edge_cache firefox_cache pip_cache")
        if name == "scan":
            command.add_argument("--json", action="store_true", help="Print the scan summary as JSON")
    subparsers.add_parser("categories", help="List supported categories and their availability")
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if os.name != "nt":
        parser.error("Clearspace currently supports Windows 10/11 only.")
    from .engine import Cleaner
    cleaner = Cleaner()
    if args.command == "categories":
        for category in cleaner.categories:
            print(f"{category.id:16} {category.label} ({'available' if category.available else category.reason or 'not found'})")
        return 0
    cancel = threading.Event()
    try:
        plan = cleaner.scan(args.categories, cancel=cancel)
        if args.command == "scan" and args.json:
            print(json.dumps(plan.to_dict(), indent=2, ensure_ascii=True))
            return 0
        print("CLEARSPACE\n")
        labels = {category.id: category.label for category in cleaner.categories}
        for category in plan.category_stats:
            print(f"{labels[category.category_id]}: {category.files} files, {_size(category.bytes)}")
            if category.reason:
                print(f"  {category.reason}")
        print(f"\nTotal file size: {_size(plan.total_bytes)} ({plan.total_files} files)")
        if args.command != "clean":
            print("Analysis only. No files were removed.")
            return 0
        if plan.cancelled or plan.truncated:
            print("Analysis was incomplete. No files were removed.", file=sys.stderr)
            return 1
        if not plan.total_files:
            print("No matching files to remove.")
            return 0
        if not sys.stdin.isatty():
            print("Cleanup requires an interactive terminal confirmation. No files were removed.", file=sys.stderr)
            return 1
        print("\nThe files above will be removed permanently.")
        if input('Type "clean" to continue: ').strip() != "clean":
            print("Cleanup cancelled. No files were removed.")
            return 0
        result = cleaner.clean(plan, args.categories, cancel=cancel)
        print(f"\nRemoved: {result.deleted_files} files ({_size(result.freed_bytes)} of file data)")
        print(f"Skipped: {result.skipped_files} | Failed: {result.failed_files}")
        print("Disk free space may differ from the size of deleted files.")
        return 1 if result.failed_files else 0
    except KeyboardInterrupt:
        cancel.set()
        print("\nOperation stopped. Any files already removed stay removed.", file=sys.stderr)
        return 130
    except (OSError, ValueError) as error:
        print(f"Clearspace: {' '.join(str(error).split())[:500]}", file=sys.stderr)
        return 1
