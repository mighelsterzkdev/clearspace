"""Build and verify a portable Windows app without launching its interface."""

from __future__ import annotations

import hashlib
from importlib import metadata
import os
from pathlib import Path
import platform
import shutil
import ssl
import subprocess
import sys
import tempfile
import zipfile

from frontend_assets import verify_frontend_assets

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("pywebview", "pythonnet", "clr_loader", "cffi", "pycparser", "proxy_tools", "bottle", "typing_extensions")


def write_notices(bundle):
    target = bundle / "licenses"
    target.mkdir()
    versions = []
    for name in PACKAGES:
        distribution = metadata.distribution(name)
        versions.append(f"{distribution.metadata['Name']} {distribution.version}")
        for entry in distribution.files or ():
            parts = Path(entry).parts
            if any(part.endswith(".dist-info") for part in parts) and any(word in str(entry).lower() for word in ("license", "copying", "notice")):
                source = Path(distribution.locate_file(entry))
                if source.is_file():
                    index = next(i for i, part in enumerate(parts) if part.endswith(".dist-info"))
                    destination = target / name / Path(*parts[index:])
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
    for source in (ROOT / "third_party").iterdir():
        if source.is_file():
            shutil.copy2(source, target / source.name)
    for name in ("LICENSE.txt", "LICENSE"):
        python_license = Path(sys.base_prefix) / name
        if python_license.is_file():
            shutil.copy2(python_license, target / "PYTHON-LICENSE.txt")
            break
    shutil.copy2(ROOT / "src/clearspace/desktop_assets/THIRD-PARTY-NOTICES.txt", target / "FRONTEND-NOTICES.txt")
    (target / "PACKAGES.txt").write_text("\n".join(versions) + "\n", encoding="utf-8")


def main():
    from clearspace import __version__
    if os.name != "nt" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("Build this portable release on Windows x64.")
    verify_frontend_assets(ROOT)
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    bundle = output / f"clearspace-{__version__}-windows-x64"
    archive = output / f"{bundle.name}.zip"
    if bundle.exists() or archive.exists():
        raise FileExistsError("A build with this version already exists. Preserve or remove it explicitly before rebuilding.")
    with tempfile.TemporaryDirectory(prefix="clearspace-build-") as temporary:
        work = Path(temporary)
        command = [sys.executable, "-B", "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
                   "--name", "clearspace", "--icon", "NONE", "--manifest", str(ROOT / "scripts/windows_gui.manifest"),
                   "--distpath", str(work / "dist"), "--workpath", str(work / "work"), "--specpath", str(work),
                   "--paths", str(ROOT / "src"), "--collect-data", "webview", "--collect-binaries", "webview",
                   "--hidden-import", "webview.platforms.edgechromium", "--hidden-import", "webview.platforms.winforms",
                   "--add-data", f"{ROOT / 'src/clearspace/desktop_assets'};clearspace/desktop_assets"]
        # Preserve the managed build runtime's CA exclusions when freezing ssl.
        exclusions = Path(ssl.__file__).with_name("codex-rejected-cas.pem")
        if exclusions.is_file():
            command += ["--add-data", f"{exclusions};."]
        for module in ("webview.platforms.qt", "webview.platforms.gtk", "webview.platforms.cocoa", "webview.platforms.cef", "webview.platforms.android", "webview.platforms.mshtml", "PyQt5", "PyQt6", "PySide2", "PySide6", "tkinter", "PIL"):
            command += ["--exclude-module", module]
        for name in PACKAGES:
            command += ["--copy-metadata", name]
        command.append(str(ROOT / "scripts/gui_entry.py"))
        subprocess.run(command, cwd=ROOT, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), check=True)
        built = work / "dist/clearspace"
        # The entry point handles this flag before starting pywebview. All test
        # deletions are confined to a TemporaryDirectory it creates itself.
        subprocess.run([str(built / "clearspace.exe"), "--self-check"], check=True, timeout=60)
        shutil.copytree(built, bundle)
    shutil.copy2(ROOT / "LICENSE", bundle / "LICENSE")
    (bundle / "start.cmd").write_text('@echo off\nsetlocal\ncd /d "%~dp0"\nstart "" "%~dp0clearspace.exe"\n', encoding="utf-8")
    (bundle / "READ-ME.txt").write_text(
        f"Clearspace {__version__} — Windows x64\n\n"
        "Extract the complete folder, then open start.cmd or clearspace.exe.\n"
        "Python and Node.js are not required. Microsoft Edge WebView2 Runtime must be installed.\n"
        "Analyze first, inspect the file list, then confirm Clean selected. Cleanup never starts automatically.\n"
        "Only supported temporary files and disk caches older than seven days are eligible.\n"
        "Files are permanently removed. Files in use, changed files, and filesystem links are skipped.\n"
        "Your personal Downloads/documents, browser sign-ins, Recycle Bin, and registry are outside the cleaner's scope.\n"
        "Displayed sizes are file data; physical free disk space may differ.\n"
        "Settings and the latest cleanup summary are stored in your LocalAppData/Clearspace folder.\n"
        "No administrator access is requested. This application is unsigned.\n"
        "Third-party notices are in licenses/.\n", encoding="utf-8")
    write_notices(bundle)
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as package:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                package.write(path, path.relative_to(output))
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    (output / "SHA256SUMS.txt").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(f"Portable self-check passed.\n{archive}\nSHA-256: {digest}")


if __name__ == "__main__":
    main()
