# Cleaning rules and references

Clearspace maintains its own explicit Python catalog. It does not download or execute third-party cleanup scripts, import a complete Winapp2 database, or classify every directory containing the word “cache” as disposable.

All production categories have minimum age **0**. Existing file locks, permissions, reparse-point exclusions, and scan identity checks still apply. The analyzer probes deletion access without setting a deletion disposition.

`LOCAL`, `ROAMING`, `LOW`, and `COMMON` below are Windows Known Folders; `WINDOWS` comes from `GetWindowsDirectoryW`. Environment variables cannot redirect these locations.

| Category | Specific scope |
|---|---|
| User temporary files | `LOCAL/Temp` |
| Windows temporary files | `WINDOWS/Temp` |
| Crash dumps | `LOCAL/CrashDumps`, only `*.dmp` |
| Error reports | `LOCAL` and `COMMON/Microsoft/Windows/WER/{ReportArchive,ReportQueue,Temp}` |
| Servicing logs | `WINDOWS/Logs/CBS`, only `CBS*.log` and `CbsPersist_*.cab`, no recursion |
| Deployment logs | `WINDOWS/Logs/DISM`, only `dism.log*`, no recursion |
| Thumbnails | `LOCAL/Microsoft/Windows/Explorer`, only `thumbcache*.db`, no recursion |
| DirectX shaders | `LOCAL/D3DSCache` |
| NVIDIA shaders | Specific `DXCache`, `GLCache`, `NV_Cache` leaves under NVIDIA's Local/LocalLow folders |
| AMD shaders | Specific `DxCache`, `DxcCache`, `GLCache`, `VkCache` leaves under AMD's Local/LocalLow folders |
| Chromium browsers | Known Chrome, Edge, Brave, Opera/GX profiles: only `Cache`, `Code Cache`, `GPUCache` |
| Firefox | Known local profile `cache2/entries` directories |
| Discord | Known Discord/PTB/Canary profiles: only `Code Cache` and `GPUCache` |
| VS Code / Cursor | Explicit rendering/compiled-code caches and cached VSIX downloads; never `User`, workspace storage, or installed extensions |
| Epic Games Launcher | Known `Saved/webcache[_digits]` profiles and optional `Default`: only rendering-cache leaves |
| pip | Default `http`, `http-v2`, `wheels` cache leaves |
| npm | Default `npm-cache/_cacache`; never projects, global modules, or `_npx` installations |
| NuGet | Default `NuGet/v3-cache` HTTP responses; never global installed packages |

## Research

Windows temporary files, reports, graphics caches, and log locations were cross-checked against [Winapp2's application definitions](https://github.com/MoscaDotTo/Winapp2/blob/master/Winapp2.ini). Those definitions also contain privacy and user-data rules that Clearspace does not implement.

Browser and app scopes were compared with BleachBit's [Explorer](https://github.com/bleachbit/bleachbit/blob/master/cleaners/windows_explorer.xml), [Brave](https://github.com/bleachbit/bleachbit/blob/master/cleaners/brave.xml), [Opera](https://github.com/bleachbit/bleachbit/blob/master/cleaners/opera.xml), and [VS Code](https://github.com/bleachbit/bleachbit/blob/master/cleaners/vscode.xml) definitions. Clearspace uses narrower file-only rules and does not restart Explorer.

BleachBit's [Discord cache definition](https://github.com/bleachbit/bleachbit/blob/master/cleaners/discord.xml) describes an impact on favorited GIFs. Clearspace therefore omits Discord's main `Cache` directory and retains only code/GPU caches.

Epic's publisher documents rebuilding its web cache in [this support article](https://www.epicgames.com/help/en-US/c-Category_EpicAccounts/c-TechnicalSupport_GeneralSupport/a000086158). Clearspace narrows that scope to rendering-cache leaves to retain profile/account storage.

Package cache behavior is documented by [pip](https://pip.pypa.io/en/stable/topics/caching/), [npm](https://docs.npmjs.com/cli/v11/commands/npm-cache/#details), and [NuGet](https://github.com/NuGet/docs.microsoft.com-nuget/blob/main/docs/consume-packages/managing-the-global-packages-and-cache-folders.md). uv is excluded because its [official cache documentation](https://github.com/astral-sh/uv/blob/main/docs/concepts/cache.md) requires coordination through its own commands and locks.

The concept of using Windows' own maintenance operations is also discussed by [Cleanmgr+](https://github.com/builtbybel/CleanmgrPlus). Windows component-store/update cleanup would need a separate native adapter; these rules do not manually erase `WinSxS`, `Installer`, or `SoftwareDistribution`.
