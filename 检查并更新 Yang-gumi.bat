@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
title Yang-gumi 手动更新程序
cd /d "%~dp0"

set "PYTHON="
if exist ".venv\Scripts\python.exe" set "PYTHON=.venv\Scripts\python.exe"
if not defined PYTHON where py >nul 2>nul && set "PYTHON=py -3"
if not defined PYTHON where python >nul 2>nul && set "PYTHON=python"

if not defined PYTHON (
  echo [错误] 没有找到 Python。请先运行“安装并启动 Yang-gumi.bat”。
  pause
  exit /b 1
)

echo ============================================================
echo              Yang-gumi 手动检查更新
echo ============================================================
%PYTHON% "%~dp0update_yanggumi.py" check
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo 更新程序返回错误代码：%RC%
pause
exit /b %RC%
