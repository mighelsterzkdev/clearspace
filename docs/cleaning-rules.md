# Cleaning rules and references

Clearspace maintains its own explicit Python catalog. It does not download or execute third-party cleanup scripts, import a complete Winapp2 database, or classify every directory containing the word “cache” as disposable.

All production categories have minimum age **0**. Existing file locks, permissions, reparse-point exclusions, and scan identity checks still apply. The analyzer probes deletion access without setting a deletion disposition.

`LOCAL`, `ROAMING`, `LOW`, and `COMMON` below are Windows Known Folders; `WINDOWS` comes from `GetWindowsDirectoryW`. Environment variables cannot redirect these locations.

| Category | Specific scope |
|---|---|
| Recycle Bin (optional) | Current user's bins across drives; native Windows Shell action, no manual `$Recycle.Bin` traversal |
| User temporary files | `LOCAL/Temp` |
| Windows temporary files | `WINDOWS/Temp` |
| Crash dumps | `LOCAL/CrashDumps`, only `*.dmp` |
| System crash diagnostics | `WINDOWS/Minidump`, `WINDOWS/LiveKernelReports`, only `*.dmp`; exact `WINDOWS/MEMORY.DMP` without recursion |
| Error reports | `LOCAL` and `COMMON/Microsoft/Windows/WER/{ReportArchive,ReportQueue,Temp}` |
| Servicing logs | `WINDOWS/Logs/CBS`, only `CBS*.log` and `CbsPersist_*.cab`, no recursion |
| Deployment logs | `WINDOWS/Logs/DISM`, only `dism.log*`, no recursion |
| Update diagnostic logs | `WINDOWS/Logs/WindowsUpdate`, `COMMON/USOShared/Logs`, only `*.etl` and `WindowsUpdate.log`, no recursion |
| Setup diagnostic logs | Fixed Panther, UnattendGC, Sysprep/Panther and Logs/MoSetup folders; explicit diagnostic log names, no traversal or configuration deletion |
| Thumbnails | `LOCAL/Microsoft/Windows/Explorer`, only `thumbcache*.db`, no recursion |
| DirectX shaders | `LOCAL/D3DSCache` |
| NVIDIA shaders | Specific `DXCache`, `GLCache`, `NV_Cache` leaves under NVIDIA's Local/LocalLow folders |
| AMD shaders | Specific `DxCache`, `DxcCache`, `GLCache`, `VkCache` leaves under AMD's Local/LocalLow folders |
| Chromium browsers | Known Chrome, Edge, Brave, Opera/GX profiles: only `Cache`, `Code Cache`, `GPUCache` |
| Firefox | Known local profile `cache2/entries` directories |
| Discord | Known Discord/PTB/Canary profiles: only `Code Cache` and `GPUCache` |
| VS Code / Cursor | Explicit rendering/compiled-code caches and cached VSIX downloads; never `User`, workspace storage, or installed extensions |
| Epic Games Launcher | Known `Saved/webcache[_digits]` profiles and optional `Default`: only rendering-cache leaves |
| Steam / Slack | Specific web rendering `Cache`, `Code Cache`, `GPUCache` leaves; browser/account stores and games are kept |
| Spotify | `Data` (streamed-song cache) and the `Browser` rendering-cache leaves, for the installer and Microsoft Store versions; never `Storage` (offline downloads) or `Users` |
| Teams / WhatsApp | Classic Teams `Cache`, `Code Cache`, `GPUCache`; new Teams and WhatsApp WebView2 profiles under their package `LocalCache`: only those leaves, never `IndexedDB`, `Local Storage`, `Network` or `LocalState` |
| pip | Default `http`, `http-v2`, `wheels` cache leaves |
| npm | Default `npm-cache/_cacache`; never projects, global modules, or `_npx` installations |
| NuGet | Default `NuGet/v3-cache` HTTP responses; never global installed packages |
| Recent-file shortcuts | `ROAMING/Microsoft/Windows/Recent`, only root `*.lnk`/`*.url`; no Jump List database traversal or original target deletion |
| Run / Explorer / RecentDocs histories | Selected history values only under the three fixed current-user Explorer history registry keys |
| Windows component cleanup | DISM `/Online /Cleanup-Image /AnalyzeComponentStore`, then confirmed `/StartComponentCleanup /NoRestart`; never `/ResetBase` or manual component-store deletion |
| Delivery Optimization | Windows module cache query and confirmed `Delete-DeliveryOptimizationCache -Force`; never `-IncludePinnedFiles` or manual cache-directory deletion |

## Research

The Windows-specific scopes follow Microsoft's [dump-file locations](https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/read-small-memory-dump-file), [live kernel dumps](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/kernel-live-dump-code-reference), [Windows Update logs](https://learn.microsoft.com/en-us/windows/deployment/update/windows-update-logs), and [setup logs](https://learn.microsoft.com/en-us/windows/deployment/upgrade/log-files). Original setup configuration is excluded. Slack leaves were compared with [BleachBit's definition](https://github.com/bleachbit/bleachbit/blob/master/cleaners/slack.xml); Steam rendering leaves with Winapp2.

Windows-owned [component cleanup](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/clean-up-the-winsxs-folder) and the [Delivery Optimization cmdlet](https://learn.microsoft.com/en-us/powershell/module/deliveryoptimization/delete-deliveryoptimizationcache) handle their respective data. Queries are bounded and read-only. Slow native queries have a separate wait budget from filesystem traversal. Native operations queue while previous work finishes. A timeout or Stop never terminates Windows servicing; the app distinguishes analysis still running from cleanup still running, marks unfinished actions pending, and requires another analysis before retrying them. The result never invents a reclaimed size. Administrator permission is required; component cleanup also checks pending restart/update markers.

History providers cover `HKCU/Software/Microsoft/Windows/CurrentVersion/Explorer/{RunMRU,WordWheelQuery,RecentDocs}`. Only recognized entry/index values are removed, with indices last; default/unknown values and keys are kept. Exact snapshots, key revisions and value contents are checked before and during clearing. There is a residual per-value comparison race with concurrent registry writers. RecentDocs subkeys are analyzed together; no Shell shortcut targets, pinned Jump Lists, CloudStore or UserAssist data is removed. Windows Search's recent apps are managed through [Search permissions](https://support.microsoft.com/en-gb/windows/privacy/windows-search-and-privacy); the app opens its documented [Settings URI](https://learn.microsoft.com/en-us/windows/apps/develop/launch/launch-settings) only after the user clicks.

Recycle Bin totals come from [SHQueryRecycleBinW](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shqueryrecyclebinw). Confirmed emptying uses [SHEmptyRecycleBinW](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shemptyrecyclebinw). The scan records aggregate items and size; those are not file identities. Changed totals skip the native action, while matching totals allow Windows to empty its current contents. The confirmation discloses possible concurrent additions. Native cleanup cannot be stopped once it starts; result totals are bounded observed changes, not a physical disk-space measurement. Tests substitute a fake bin provider and never empty the real bin.

Windows temporary files, reports, graphics caches, and log locations were cross-checked against [Winapp2's application definitions](https://github.com/MoscaDotTo/Winapp2/blob/master/Winapp2.ini). Those definitions also contain privacy and user-data rules that Clearspace does not implement.

Browser and app scopes were compared with BleachBit's [Explorer](https://github.com/bleachbit/bleachbit/blob/master/cleaners/windows_explorer.xml), [Brave](https://github.com/bleachbit/bleachbit/blob/master/cleaners/brave.xml), [Opera](https://github.com/bleachbit/bleachbit/blob/master/cleaners/opera.xml), and [VS Code](https://github.com/bleachbit/bleachbit/blob/master/cleaners/vscode.xml) definitions. Clearspace uses narrower file-only rules and does not restart Explorer.

BleachBit's [Discord cache definition](https://github.com/bleachbit/bleachbit/blob/master/cleaners/discord.xml) describes an impact on favorited GIFs. Clearspace therefore omits Discord's main `Cache` directory and retains only code/GPU caches.

Epic's publisher documents rebuilding its web cache in [this support article](https://www.epicgames.com/help/en-US/c-Category_EpicAccounts/c-TechnicalSupport_GeneralSupport/a000086158). Clearspace narrows that scope to rendering-cache leaves to retain profile/account storage.

Spotify keeps offline downloads in its `Storage` folder and rebuilds `Data` while streaming, as BleachBit's [Spotify definition](https://github.com/bleachbit/bleachbit/blob/master/cleaners/spotify.xml) and Winapp2 also treat it. Teams and WhatsApp desktop render through WebView2 or Chromium profiles; Clearspace removes the same rendering-cache leaves as for browsers and keeps chat databases, local storage, cookies and received media. Microsoft's documented [Teams cache reset](https://learn.microsoft.com/en-us/troubleshoot/microsoftteams/teams-administration/clear-teams-cache) clears the whole folder and signs the user out, which Clearspace deliberately does not do.

Package cache behavior is documented by [pip](https://pip.pypa.io/en/stable/topics/caching/), [npm](https://docs.npmjs.com/cli/v11/commands/npm-cache/#details), and [NuGet](https://github.com/NuGet/docs.microsoft.com-nuget/blob/main/docs/consume-packages/managing-the-global-packages-and-cache-folders.md). uv is excluded because its [official cache documentation](https://github.com/astral-sh/uv/blob/main/docs/concepts/cache.md) requires coordination through its own commands and locks.

The concept of using Windows' own maintenance operations is also discussed by [Cleanmgr+](https://github.com/builtbybel/CleanmgrPlus). The native component adapter above does not manually erase `WinSxS`, `Installer`, or `SoftwareDistribution`. Windows.old, restore points, installed-driver packages, browser sessions and personal files are outside these cleanup rules.
