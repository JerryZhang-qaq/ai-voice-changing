@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title VoiceWorkbench diagnostics
cd /d "%~dp0"
echo Starting VoiceWorkbench diagnostics...
if not exist "%~dp0.venv\Scripts\python.exe" goto missing
"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\doctor.py"
set "workbench_exit=%errorlevel%"
if not "%workbench_exit%"=="0" echo [ERROR] Command failed. Exit code: %workbench_exit%
goto finish
:missing
echo [ERROR] Python environment is missing. Run Install-Windows.cmd first.
set "workbench_exit=1"
:finish
echo.
echo Press any key to close this window.
pause >nul
exit /b %workbench_exit%
