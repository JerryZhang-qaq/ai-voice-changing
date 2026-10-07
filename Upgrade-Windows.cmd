@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
call "%~dp0Install-Windows.cmd" -Upgrade %*
exit /b %errorlevel%
