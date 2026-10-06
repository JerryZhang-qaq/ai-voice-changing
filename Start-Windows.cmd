@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo 请先运行 Install-Windows.cmd。
  pause
  exit /b 1
)
.venv\Scripts\python.exe scripts\launch.py
if errorlevel 1 pause
