@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0dist\clearspace-0.1.0-windows-x64\clearspace.exe" (
    start "" "%~dp0dist\clearspace-0.1.0-windows-x64\clearspace.exe"
    exit /b 0
)
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo Clearspace is not installed. Run install.cmd first.
    pause
    exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" -B -m clearspace.gui
