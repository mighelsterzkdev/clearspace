@echo off
setlocal
set "CLEARSPACE_ADMIN_LAUNCHER=%~dp0start.cmd"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -Command "try { Start-Process -FilePath $env:CLEARSPACE_ADMIN_LAUNCHER -Verb RunAs -WindowStyle Hidden -ErrorAction Stop } catch { Write-Host 'Administrator launch was cancelled or failed.'; exit 1 }"
