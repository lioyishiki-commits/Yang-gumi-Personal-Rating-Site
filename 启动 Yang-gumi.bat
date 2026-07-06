@echo off
chcp 65001 >nul
title Yang-gumi 本地评分库
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" start_yanggumi.py
    goto launch_result
)

where python >nul 2>nul
if errorlevel 1 (
    echo 未找到 Python。请先安装 Python 3.13.x 64 位完整版。
    echo 安装完成后再双击本文件。
    pause
    exit /b 1
)

python start_yanggumi.py

:launch_result
if errorlevel 1 (
    echo.
    echo Yang-gumi 未能正常启动，请保留此窗口并截图上方错误信息。
    pause
)
