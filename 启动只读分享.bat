@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "YANGGUMI_LAN_URL=http://192.168.81.1:8502/"
set "YANGGUMI_PORT=8502"

rem In the project folder, start the owner's read-only server.
rem As a standalone file on another Windows PC or VM, open the owner's site.
if exist "%~dp0share_public.py" goto owner
goto visitor

:visitor
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='SilentlyContinue';" ^
  "$source=[Uri]$env:YANGGUMI_LAN_URL;" ^
  "$suffix=$source.PathAndQuery;" ^
  "$urls=New-Object 'System.Collections.Generic.List[string]';" ^
  "$urls.Add($source.AbsoluteUri);" ^
  "$ips=[Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() | ForEach-Object {$_.GetIPProperties().UnicastAddresses} | ForEach-Object {$_.Address} | Where-Object {$_.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork -and -not $_.ToString().StartsWith('127.') -and -not $_.ToString().StartsWith('169.254.')};" ^
  "foreach($ip in $ips){$part=$ip.ToString().Split('.');$url='http://'+$part[0]+'.'+$part[1]+'.'+$part[2]+'.1:'+$env:YANGGUMI_PORT+$suffix;if(-not $urls.Contains($url)){$urls.Add($url)}};" ^
  "$edge=[IO.Path]::Combine(${env:ProgramFiles(x86)},'Microsoft','Edge','Application','msedge.exe');if(-not [IO.File]::Exists($edge)){$edge=[IO.Path]::Combine($env:ProgramFiles,'Microsoft','Edge','Application','msedge.exe')};" ^
  "foreach($url in $urls){try{$request=[Net.HttpWebRequest]::Create($url);$request.Proxy=$null;$request.Timeout=8000;$request.AllowAutoRedirect=$true;$response=$request.GetResponse();$status=[int]$response.StatusCode;$response.Close();if($status -ge 200 -and $status -lt 400){if([IO.File]::Exists($edge)){Start-Process -FilePath $edge -ArgumentList @('--proxy-server=direct://','--proxy-bypass-list=*',$url)}else{Start-Process $url};exit 0}}catch{}};" ^
  "exit 1"
if not errorlevel 1 exit /b 0
echo Yang-gumi read-only site is not reachable.
echo Keep the owner-side share process running, then run this file again.
pause
exit /b 1

:owner
cd /d "%~dp0"
netsh advfirewall firewall show rule name="Yang-gumi Read-only 8502" >nul 2>nul
if not errorlevel 1 goto owner_python
powershell.exe -NoLogo -NoProfile -Command "Start-Process netsh -Verb RunAs -Wait -ArgumentList 'advfirewall firewall add rule name=\"Yang-gumi Read-only 8502\" dir=in action=allow protocol=TCP localport=8502 profile=private'"

:owner_python
if exist "%~dp0.venv\Scripts\python.exe" goto owner_dotvenv
if exist "%~dp0venv\Scripts\python.exe" goto owner_venv
if exist "%USERPROFILE%\miniconda3\python.exe" goto owner_miniconda
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" goto owner_python313
where python >nul 2>nul
if not errorlevel 1 goto owner_python_path
where py >nul 2>nul
if not errorlevel 1 goto owner_py_launcher
echo Python for the owner-side website was not found.
echo Run the setup launcher on the owner PC once.
goto done

:owner_dotvenv
"%~dp0.venv\Scripts\python.exe" "%~dp0share_public.py"
goto done

:owner_venv
"%~dp0venv\Scripts\python.exe" "%~dp0share_public.py"
goto done

:owner_miniconda
"%USERPROFILE%\miniconda3\python.exe" "%~dp0share_public.py"
goto done

:owner_python313
"%LOCALAPPDATA%\Programs\Python\Python313\python.exe" "%~dp0share_public.py"
goto done

:owner_python_path
python "%~dp0share_public.py"
goto done

:owner_py_launcher
py -3 "%~dp0share_public.py"

:done
pause
