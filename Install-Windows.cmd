@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title VoiceWorkbench Installer
cd /d "%~dp0"
echo Starting VoiceWorkbench Windows installer...
echo.
if not exist "%~dp0scripts\install-windows.ps1" goto missing
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-windows.ps1"
set "workbench_exit=%errorlevel%"
echo.
if "%workbench_exit%"=="0" (
  echo Installation completed. Run Start-Windows.cmd to start the workbench.
) else (
  echo [ERROR] Installation failed. Exit code: %workbench_exit%
  echo See the messages above and runtime\diagnostics\install-*.log.
)
goto finish
:missing
echo [ERROR] scripts\install-windows.ps1 is missing.
echo Extract the entire ZIP to a folder before running this file.
set "workbench_exit=1"
:finish
echo.
echo Press any key to close this window.
pause >nul
exit /b %workbench_exit%
