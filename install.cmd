@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
    if errorlevel 1 (
        echo Install Python 3.12 or newer, then run this script again.
        pause
        exit /b 1
    )
    py -3 -m venv .venv
    if errorlevel 1 (
        echo Install Python 3.12 or newer, then run this script again.
        pause
        exit /b 1
    )
)
".venv\Scripts\python.exe" -B -m pip install -c constraints.txt -e .
if errorlevel 1 exit /b 1
echo Clearspace is ready. Open start.cmd.
pause
