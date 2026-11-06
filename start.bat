@echo off
setlocal
cd /d "%~dp0"
rem Finish this batch before the server console receives Ctrl+C.
start "Anju Vision" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
exit /b %errorlevel%
