@echo off
chcp 65001 >nul
title Yang-gumi 本地评分库
cd /d "%~dp0"
rem Compatibility entry point: python start_yanggumi.py

if not exist ".venv\Scripts\python.exe" goto setup
".venv\Scripts\python.exe" -c "import streamlit" >nul 2>nul
if errorlevel 1 goto setup

".venv\Scripts\python.exe" "%~dp0start_yanggumi.py"
if not errorlevel 1 exit /b 0
echo.
echo Yang-gumi failed to start. Keep this window open.
pause
exit /b 1

:setup
call "%~dp0安装并启动 Yang-gumi.bat"
exit /b %errorlevel%
