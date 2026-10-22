# Clearspace

A small Windows cleaner built with Python and a dark React interface using Radix/shadcn components. Analyze supported locations, review individual files, then remove only the selected results.

![Clearspace desktop interface](docs/clearspace.png)

## First release

- User temporary files older than seven days, selected by default.
- Optional disk caches for supported Chrome, Edge, and Firefox profiles, plus the default pip HTTP cache.
- File previews, category totals, cancellation, and a result summary.
- Active applications, locked files, changed files, links, and unsupported locations are skipped.
- Selected categories are remembered locally. Cleanup never starts automatically.

Clearspace removes files permanently after the review confirmation. It does not clean the registry, empty the Recycle Bin, remove personal Downloads/documents, erase browser cookies/passwords, or elevate to administrator. The first release supports local Windows 10/11 x64 installations.

Sizes represent file data, not a guarantee of physically reclaimed disk space. Filesystem allocation, compression, open handles, and other running programs can affect free space.

## Run

Open `start.cmd` for the desktop app. The included portable Windows folder can run without installing Python or Node.js; Microsoft Edge WebView2 Runtime is required and normally comes with Windows 11.

For a source checkout, install Python 3.12 or newer, run `install.cmd`, then open `start.cmd`. Generated frontend assets are included, so Node.js is needed only when changing the interface.

```powershell
.\.venv\Scripts\python.exe -B -m clearspace scan
.\.venv\Scripts\python.exe -B -m clearspace clean
```

The terminal cleaner analyzes first and requires typing `clean` after showing its results. Running `scan` never deletes files.

## Development

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -B -m pip install -c constraints.txt -e ".[build]"
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
cd desktop-ui
pnpm install --frozen-lockfile
pnpm test
pnpm build
```

The Python service exposes a narrow in-process bridge to a local renderer. Browser content cannot submit deletion paths: cleanup accepts an existing scan ID and supported category IDs. Scan, filesystem validation, and deletion live in the Python engine, independently of the interface.

Build the portable application with `python -B scripts/build_windows.py`. Build outputs are ignored by Git. Settings and summary reports are saved under the current user's local application data in `Clearspace`.

## References

The workflow draws inspiration from [BleachBit](https://www.bleachbit.org/features), [Microsoft PC Manager](https://pcmanager.microsoft.com/en-us), and [Windows Storage Sense](https://support.microsoft.com/en-us/windows/experience/storage-filemanagement/manage-drive-space-with-storage-sense). Clearspace is an independent implementation.

MIT licensed. Third-party component notices accompany the frontend and portable package.
