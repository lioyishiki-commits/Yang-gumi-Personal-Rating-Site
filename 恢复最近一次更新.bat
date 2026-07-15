@echo off
setlocal
chcp 65001 >nul
title Yang-gumi 恢复更新前版本
cd /d "%~dp0"

set "PYTHON="
if exist ".venv\Scripts\python.exe" set "PYTHON=.venv\Scripts\python.exe"
if not defined PYTHON where py >nul 2>nul && set "PYTHON=py -3"
if not defined PYTHON where python >nul 2>nul && set "PYTHON=python"

if not defined PYTHON (
  echo [错误] 没有找到 Python。
  pause
  exit /b 1
)

%PYTHON% "%~dp0update_yanggumi.py" rollback
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
