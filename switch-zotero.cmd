@echo off
setlocal
rem No argument toggles formal/development. Explicit targets: dev or formal.
powershell.exe -NoLogo -NoProfile -File "%~dp0scripts\dev.ps1" switch %*
set "switch_result=%errorlevel%"
if not "%switch_result%"=="0" (
    echo.
    echo Switch failed. See the message above; press any key to close.
    pause >nul
)
exit /b %switch_result%
