@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "YANGGUMI_PUBLIC_URL="
set "YANGGUMI_SHARE_LOCATOR="

rem One file, two modes:
rem - In the Yang-gumi project folder, start or open the owner's live share.
rem - As the only file on another Windows PC or VM, open that live share.
if exist "%~dp0share_public.py" goto owner
goto visitor

:visitor
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue';[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;$u=$env:YANGGUMI_PUBLIC_URL;try{$d=Invoke-RestMethod -UseBasicParsing -Uri $env:YANGGUMI_SHARE_LOCATOR -TimeoutSec 8;if($d.active -eq $false){exit 2};if($d.url -match '^https://[a-z0-9.-]+\.(plaintunnel\.com|serveousercontent\.com|runlocal\.eu|xpos\.to|wormhole\.bar|expose\.host|hostc\.dev|free\.pinggy\.net|run\.pinggy-free\.link|lhr\.life|trycloudflare\.com)/(?:.*[?&]access=|_yanggumi_share/[A-Za-z0-9_-]+\?serveo-skip-browser-warning=true)'){$u=[string]$d.url}}catch{};if($u -notmatch '^https://[a-z0-9.-]+\.(plaintunnel\.com|serveousercontent\.com|runlocal\.eu|xpos\.to|wormhole\.bar|expose\.host|hostc\.dev|free\.pinggy\.net|run\.pinggy-free\.link|lhr\.life|trycloudflare\.com)/(?:.*[?&]access=|_yanggumi_share/[A-Za-z0-9_-]+\?serveo-skip-browser-warning=true)'){exit 2};if($env:SHIKISHARE_VISITOR_DRY_RUN){[Console]::Write($u);exit 0};try{$p=New-Object System.Diagnostics.ProcessStartInfo;$p.FileName=$u;$p.UseShellExecute=$true;[Diagnostics.Process]::Start($p)|Out-Null;exit 0}catch{exit 3}"
set "YANGGUMI_VISITOR_EXIT=%ERRORLEVEL%"
if "%YANGGUMI_VISITOR_EXIT%"=="2" goto missing_public_url
if not "%YANGGUMI_VISITOR_EXIT%"=="0" goto visitor_failed
exit /b 0

:owner
cd /d "%~dp0"
set "PYTHON_EXE="
set "PYTHON_ARG="
if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%~dp0venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%USERPROFILE%\miniconda3\python.exe" set "PYTHON_EXE=%USERPROFILE%\miniconda3\python.exe"
if not defined PYTHON_EXE if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PYTHON_EXE where python >nul 2>nul && set "PYTHON_EXE=python"
if not defined PYTHON_EXE where py >nul 2>nul && set "PYTHON_EXE=py" && set "PYTHON_ARG=-3"
if not defined PYTHON_EXE goto missing_python

echo Starting the secure read-only proxy for Yang-gumi...
set "PYTHONUTF8=1"
if defined SHIKISHARE_DRY_RUN exit /b 0
"%PYTHON_EXE%" %PYTHON_ARG% "%~dp0share_public.py"
if errorlevel 1 goto share_failed
exit /b 0

:missing_public_url
echo This visitor copy does not contain a live Yang-gumi share link.
echo Start sharing on the owner PC, then copy this same file again.
pause
exit /b 2

:visitor_failed
echo The default browser could not open the Yang-gumi read-only site.
echo Keep the owner-side share window open, then run this file again.
pause
exit /b 3

:missing_python
echo Python required by the owner-side Yang-gumi project was not found.
echo Install the Yang-gumi prerequisites first.
pause
exit /b 4

:share_failed
echo The secure read-only share could not be started.
echo The existing database was not modified.
pause
exit /b 5
