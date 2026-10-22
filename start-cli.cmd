@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Clearspace is not installed. Run install.cmd first.
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -B -m clearspace %*
exit /b %errorlevel%
