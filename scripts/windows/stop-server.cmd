@echo off
chcp 65001 >nul
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-server.ps1" %*
set "exit_code=%ERRORLEVEL%"
pause
exit /b %exit_code%
