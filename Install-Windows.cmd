@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\install-windows.ps1
if errorlevel 1 echo 安装失败，请查看上面的错误及 docs\windows.md。
pause
