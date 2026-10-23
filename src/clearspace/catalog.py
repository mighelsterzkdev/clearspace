"""Independently maintained, specific cache locations; never imported scripts.

Locations were cross-checked against BleachBit, Winapp2, and publisher docs.
See docs/cleaning-rules.md for references and intentional exclusions.
Only Windows-known folders and fixed application subdirectories are used.
"""

from __future__ import annotations

import os
from pathlib import Path
import re

from . import windows


def _profiles(base: Path, pattern: str) -> tuple[Path, ...]:
    try:
        with windows.pin_directories(base):
            with os.scandir(base) as children:
                names = [child.name for child in children if re.fullmatch(pattern, child.name)]
        return tuple(base / name for name in sorted(names, key=str.casefold))
    except OSError:
        return ()


def discover_categories(locations=None):
    """The optional folder mapping is an internal sandbox-test dependency."""
    from .engine import Category

    if locations is None:
        locations = {
            "local": windows.local_app_data(), "roaming": windows.roaming_app_data(),
            "low": windows.local_low_app_data(), "common": windows.common_app_data(),
            "windows": windows.windows_directory(),
        }
    local, roaming, low, common, win = (Path(locations[key]) for key in ("local", "roaming", "low", "common", "windows"))
    categories = []

    def add(identifier, title, description, paths, *, group="Windows", recommended=False,
            processes=(), patterns=(), recursive=True):
        found = []
        permission_needed = False
        for path in dict.fromkeys(Path(path) for path in paths):
            try:
                with windows.pin_directories(path):
                    found.append(path)
            except OSError as error:
                permission_needed |= getattr(error, "winerror", None) == 5
        reason = None if found else "Administrator permission required." if permission_needed else "No supported cache folder found."
        categories.append(Category(
            identifier, title, description, tuple(found), default_selected=recommended,
            processes=tuple(processes), minimum_age_days=0, reason=reason,
            file_patterns=tuple(patterns), recursive=recursive, group=group,
        ))

    add("user_temp", "Temporary files", "Temporary files that can be removed now.", [local / "Temp"], recommended=True)
    add("windows_temp", "Windows temporary files", "System temporary files. Protected or locked files are skipped.", [win / "Temp"], recommended=True)
    add("crash_dumps", "Crash dumps", "Diagnostic memory dumps left by crashed applications.", [local / "CrashDumps"], patterns=("*.dmp",), recommended=True)
    add("error_reports", "Windows error reports", "Queued and archived diagnostic reports.", [
        base / "Microsoft/Windows/WER" / name
        for base in (local, common) for name in ("ReportArchive", "ReportQueue", "Temp")
    ], recommended=True)
    add("windows_cbs_logs", "Windows servicing logs", "CBS diagnostic logs and archived logs.", [win / "Logs/CBS"],
        patterns=("CBS*.log", "CbsPersist_*.cab"), recursive=False)
    add("windows_dism_logs", "Windows deployment logs", "DISM diagnostic log files.", [win / "Logs/DISM"], patterns=("dism.log*",), recursive=False)
    add("thumbnail_cache", "Thumbnail cache", "Explorer image previews. Thumbnails will be generated again.", [local / "Microsoft/Windows/Explorer"],
        patterns=("thumbcache*.db",), recursive=False)
    add("directx_cache", "DirectX shader cache", "Compiled shaders. Games may rebuild these on the next launch.", [local / "D3DSCache"])
    add("nvidia_cache", "NVIDIA shader cache", "Compiled shaders and graphics cache; regenerated when needed.", [
        local / "NVIDIA/DXCache", local / "NVIDIA/GLCache", local / "NVIDIA Corporation/NV_Cache",
        low / "NVIDIA/DXCache", low / "NVIDIA/PerDriverVersion/DXCache",
    ])
    add("amd_cache", "AMD shader cache", "Compiled shaders and graphics cache; regenerated when needed.", [
        local / "AMD/DxCache", local / "AMD/DxcCache", local / "AMD/GLCache", local / "AMD/VkCache", low / "AMD/DxCache",
    ])

    def chromium(identifier, title, base, processes):
        profiles = _profiles(base, r"Default|Profile [0-9]+")
        add(identifier, title, "Web page and rendering caches. Close the browser before cleaning.",
            [profile / leaf for profile in profiles for leaf in ("Cache", "Code Cache", "GPUCache")],
            group="Browsers", recommended=True, processes=processes)

    chromium("chrome_cache", "Google Chrome", local / "Google/Chrome/User Data", ("chrome.exe",))
    chromium("edge_cache", "Microsoft Edge", local / "Microsoft/Edge/User Data", ("msedge.exe",))
    chromium("brave_cache", "Brave", local / "BraveSoftware/Brave-Browser/User Data", ("brave.exe",))
    firefox = _profiles(local / "Mozilla/Firefox/Profiles", r"[A-Za-z0-9._-]+")
    add("firefox_cache", "Mozilla Firefox", "Web page cache. Close Firefox before cleaning.",
        [profile / "cache2/entries" for profile in firefox], group="Browsers", recommended=True, processes=("firefox.exe",))
    opera = [base / "Opera Software" / name for base in (local, roaming) for name in ("Opera Stable", "Opera GX Stable")]
    add("opera_cache", "Opera / Opera GX", "Web page and rendering caches. Close Opera before cleaning.", [
        profile / subprofile / leaf for profile in opera for subprofile in ("", "Default") for leaf in ("Cache", "Code Cache", "GPUCache")
    ], group="Browsers", recommended=True, processes=("opera.exe", "opera_crashreporter.exe"))

    add("discord_cache", "Discord", "Rendering cache only. Favorites and account data are kept.", [
        roaming / name / leaf for name in ("discord", "discordptb", "discordcanary") for leaf in ("Code Cache", "GPUCache")
    ], group="Applications", processes=("Discord.exe", "DiscordPTB.exe", "DiscordCanary.exe"))
    editor_leaves = ("Cache", "CachedData", "Code Cache", "GPUCache", "CachedExtensionVSIXs")
    add("vscode_cache", "Visual Studio Code", "Rendering cache and cached extension downloads. Projects are kept.", [
        roaming / name / leaf for name in ("Code", "Code - Insiders") for leaf in editor_leaves
    ], group="Applications", processes=("Code.exe", "Code - Insiders.exe"))
    add("cursor_cache", "Cursor", "Rendering cache and cached extension downloads. Projects are kept.", [roaming / "Cursor" / leaf for leaf in editor_leaves],
        group="Applications", processes=("Cursor.exe",))
    epic = _profiles(local / "EpicGamesLauncher/Saved", r"webcache(?:_[0-9]+)?")
    add("epic_cache", "Epic Games Launcher", "Rendering caches. Close Epic Games Launcher before cleaning.", [
        profile / subprofile / leaf for profile in epic for subprofile in ("", "Default") for leaf in ("Cache", "Code Cache", "GPUCache")
    ], group="Applications", processes=("EpicGamesLauncher.exe", "EpicWebHelper.exe"))

    add("pip_cache", "Python package cache", "Cached downloads and built wheels. Installed packages are kept.", [
        local / "pip/Cache" / leaf for leaf in ("http", "http-v2", "wheels")
    ], group="Developer tools", processes=("pip.exe", "pip3.exe"))
    add("npm_cache", "npm download cache", "Downloaded package content. Projects and installed modules are kept.", [local / "npm-cache/_cacache"], group="Developer tools")
    add("nuget_cache", "NuGet HTTP cache", "Downloaded HTTP responses. Installed packages are kept.", [local / "NuGet/v3-cache"], group="Developer tools", processes=("nuget.exe", "dotnet.exe", "devenv.exe", "MSBuild.exe"))
    return categories
