@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist "share_public.py" (
  echo 找不到 share_public.py，请确认本文件位于 Yang-gumi 项目根目录。
  pause
  exit /b 1
)

rem Windows may request administrator approval once to allow private-network access on port 8502.
netsh advfirewall firewall show rule name="Yang-gumi Read-only 8502" >nul 2>nul
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process netsh -Verb RunAs -Wait -ArgumentList 'advfirewall firewall add rule name=\"Yang-gumi Read-only 8502\" dir=in action=allow protocol=TCP localport=8502 profile=private'"
)

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" share_public.py
  goto done
)

where python >nul 2>nul
if errorlevel 1 (
  echo 未找到可用的 Python 环境。请先双击“安装并启动 Yang-gumi.bat”。
  goto done
)
python share_public.py

:done
pause
