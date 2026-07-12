@echo off
chcp 65001 >nul
title 卸载 Yang-gumi
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" goto run_uninstaller
echo 未找到 Yang-gumi 的独立运行环境，无法启动图形卸载程序。
echo 如程序从未安装，可直接删除当前文件夹。
pause
exit /b 1

:run_uninstaller
start "" ".venv\Scripts\pythonw.exe" "%~dp0uninstall_yanggumi.py"
exit /b 0
