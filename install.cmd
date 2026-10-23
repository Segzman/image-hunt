@echo off
rem Double-click to install Image Hunt on Windows. Pass -Uninstall to remove it.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
pause
