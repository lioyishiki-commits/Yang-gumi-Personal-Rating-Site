@echo off
chcp 65001 >nul
title 安装并启动 Yang-gumi
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto install

where py >nul 2>nul
if errorlevel 1 goto try_python
py -3.13 -m venv .venv
if not errorlevel 1 goto venv_ready
py -3 -m venv .venv
goto venv_ready

:try_python
where python >nul 2>nul
if errorlevel 1 goto no_python
python -m venv .venv

:venv_ready
if errorlevel 1 goto venv_failed

:install
".venv\Scripts\python.exe" -c "import struct,sys; assert sys.version_info >= (3,10) and struct.calcsize('P') * 8 == 64"
if errorlevel 1 goto unsupported_python
echo 正在准备 Yang-gumi 的独立运行环境……
set "PIP_NO_DEPS=false"
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto install_failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto install_failed

echo.
echo 安装完成，正在打开 Yang-gumi……
".venv\Scripts\python.exe" start_yanggumi.py
if errorlevel 1 goto launch_failed
exit /b 0

:no_python
echo 未找到 Python。请先从 https://www.python.org/downloads/ 安装 Python 3.11 或更高版本。
echo 安装时请勾选 Add Python to PATH，然后重新双击本文件。
goto pause_and_exit

:unsupported_python
echo Yang-gumi requires 64-bit Python 3.10-3.14; Python 3.13.x 64-bit is recommended.
echo Remove the incorrect 32-bit install, delete .venv, and run this file again.
goto pause_and_exit

:venv_failed
echo 无法创建 .venv 独立环境，请确认 Python 安装完整。
goto pause_and_exit

:install_failed
echo 依赖安装失败。请检查网络，并保留此窗口中的错误信息。
goto pause_and_exit

:launch_failed
echo Yang-gumi 未能正常启动，请保留此窗口并截图上方错误信息。

:pause_and_exit
pause
exit /b 1
