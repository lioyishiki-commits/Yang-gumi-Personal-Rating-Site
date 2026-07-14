param([switch]$CleanInstall)

$ErrorActionPreference = 'Stop'

$Branch = 'agent/fix-new-pc-deployment'
$Repository = 'lioyishiki-commits/Yang-gumi-Personal-Rating-Site'
$Desktop = [Environment]::GetFolderPath('Desktop')
$Pictures = [Environment]::GetFolderPath('MyPictures')
$Target = Join-Path $Desktop 'Yang-gumi-Personal-Rating-Site-GitHub-fixed'
$Report = Join-Path $Desktop 'github-install-result.json'
$Temp = Join-Path $env:TEMP ('yanggumi-github-' + [Guid]::NewGuid().ToString('N'))
$Zip = Join-Path $Temp 'patch.zip'
$Extract = Join-Path $Temp 'source'
$ImageExtensions = @('.jpg', '.jpeg', '.jfif', '.png', '.webp', '.avif', '.bmp', '.gif')
$PortraitNames = @(([string][char]0x7AD6 + [char]0x5C4F), ([string][char]0x7AD6 + [char]0x56FE), 'Portrait', 'portrait')
$WallpaperNames = @(([string][char]0x58C1 + [char]0x7EB8), 'Wallpaper', 'wallpaper')

function Write-Json($Path, $Value) {
    [IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($false)))
}

function Image-Count($Path) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Container)) { return 0 }
    return @(Get-ChildItem -LiteralPath $Path -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $ImageExtensions -contains $_.Extension.ToLowerInvariant() }).Count
}

function Local-Image-Folder([string[]]$Names) {
    foreach ($Base in @($Desktop, $Pictures) | Where-Object { $_ }) {
        foreach ($Name in $Names) {
            $Candidate = Join-Path $Base $Name
            if ((Image-Count $Candidate) -gt 0) { return $Candidate }
        }
    }
    return $null
}

$Result = [ordered]@{ status = 'running'; source = 'github'; branch = $Branch; clean_install = [bool]$CleanInstall; started_at = (Get-Date).ToString('s') }
Write-Json $Report $Result
try {
    New-Item -ItemType Directory -Path $Temp, $Extract -Force | Out-Null
    $Url = "https://github.com/$Repository/archive/refs/heads/$Branch.zip"
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Zip -TimeoutSec 180
    Expand-Archive -LiteralPath $Zip -DestinationPath $Extract -Force
    $Source = Get-ChildItem -LiteralPath $Extract -Directory | Select-Object -First 1
    if (-not $Source -or -not (Test-Path -LiteralPath (Join-Path $Source.FullName 'app.py'))) {
        throw 'GitHub patch archive is invalid.'
    }

    $Roots = @($Desktop, (Join-Path $env:USERPROFILE 'Downloads')) | Where-Object { Test-Path -LiteralPath $_ }
    $Candidates = foreach ($Root in $Roots) {
        $Top = @(Get-ChildItem -LiteralPath $Root -Directory -ErrorAction SilentlyContinue)
        $Top + @($Top | ForEach-Object { Get-ChildItem -LiteralPath $_.FullName -Directory -ErrorAction SilentlyContinue }) |
            Where-Object { $_.FullName -ne $Target -and (Test-Path -LiteralPath (Join-Path $_.FullName 'app.py')) }
    }
    $Old = $Candidates | Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'data\acgn.db') } |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1

    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -in @('python.exe', 'pythonw.exe', 'py.exe') -and $_.CommandLine -and
        ($_.CommandLine.Contains($Target) -or ($_.CommandLine -match 'streamlit\s+run' -and $_.CommandLine -match '8501'))
    } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2

    if ($CleanInstall) {
        $Removed = @()
        foreach ($Root in $Roots) {
            $RootPath = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
            foreach ($Directory in @(Get-ChildItem -LiteralPath $Root -Directory -ErrorAction SilentlyContinue)) {
                $Resolved = [IO.Path]::GetFullPath($Directory.FullName)
                if (
                    $Directory.Name -like 'Yang-gumi*' -and
                    $Resolved.StartsWith($RootPath, [StringComparison]::OrdinalIgnoreCase) -and
                    (Test-Path -LiteralPath (Join-Path $Resolved 'app.py'))
                ) {
                    Remove-Item -LiteralPath $Resolved -Recurse -Force
                    $Removed += $Resolved
                }
            }
        }
        $Result.removed_installs = $Removed
        $Old = $null
    }

    if (Test-Path -LiteralPath $Target) {
        if ($CleanInstall) {
            Remove-Item -LiteralPath $Target -Recurse -Force
        } else {
            $Backup = "$Target-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
            Move-Item -LiteralPath $Target -Destination $Backup
            $Result.previous_target = $Backup
        }
    }
    Copy-Item -LiteralPath $Source.FullName -Destination $Target -Recurse

    if ($Old -and -not $CleanInstall) {
        $OldPath = $Old.FullName
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $_.Name -in @('python.exe', 'pythonw.exe', 'py.exe') -and $_.CommandLine -and $_.CommandLine.Contains($OldPath)
        } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        foreach ($Name in @('data', 'covers', 'backgrounds', 'backups')) {
            $From = Join-Path $OldPath $Name
            $To = Join-Path $Target $Name
            if (Test-Path -LiteralPath $From) {
                New-Item -ItemType Directory -Path $To -Force | Out-Null
                Copy-Item -Path (Join-Path $From '*') -Destination $To -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
        $Result.old_install = $OldPath
    }

    $Portrait = Local-Image-Folder $PortraitNames
    $Wallpaper = Local-Image-Folder $WallpaperNames
    $Settings = [ordered]@{}
    if ($Portrait) { $Settings.portrait = $Portrait }
    if ($Wallpaper) { $Settings.wallpaper = $Wallpaper }
    if ($Settings.Count -gt 0) {
        $Data = Join-Path $Target 'data'
        New-Item -ItemType Directory -Path $Data -Force | Out-Null
        Write-Json (Join-Path $Data 'daily_art_settings.json') $Settings
        Remove-Item -LiteralPath (Join-Path $Data 'image_manifest.json') -Force -ErrorAction SilentlyContinue
    }

    $Result.portrait_folder = $Portrait
    $Result.portrait_images = Image-Count $Portrait
    $Result.wallpaper_folder = $Wallpaper
    $Result.wallpaper_images = Image-Count $Wallpaper
    $Launcher = Get-ChildItem -LiteralPath $Target -Filter '*.bat' -File | Where-Object {
        (Get-Content -LiteralPath $_.FullName -Raw) -match 'pip install -r requirements\.txt'
    } | Select-Object -First 1
    if (-not $Launcher) { throw 'Yang-gumi launcher is missing.' }
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -in @('python.exe', 'pythonw.exe', 'py.exe') -and $_.CommandLine -and
        $_.CommandLine -match 'streamlit\s+run' -and $_.CommandLine -match '8501'
    } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
    Start-Process -FilePath $Launcher.FullName -WorkingDirectory $Target -WindowStyle Hidden

    $Deadline = (Get-Date).AddMinutes(10)
    $Healthy = $false
    $ManifestItems = 0
    do {
        Start-Sleep -Seconds 2
        try { $Healthy = (Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8501/_stcore/health' -TimeoutSec 2).StatusCode -eq 200 } catch { $Healthy = $false }
        $Manifest = Join-Path $Target 'data\image_manifest.json'
        if (Test-Path -LiteralPath $Manifest) {
            try { $ManifestItems = @((Get-Content -LiteralPath $Manifest -Raw | ConvertFrom-Json).items).Count } catch { $ManifestItems = 0 }
        }
    } until (($Healthy -and (($Result.portrait_images + $Result.wallpaper_images) -eq 0 -or $ManifestItems -gt 0)) -or (Get-Date) -ge $Deadline)

    if (-not $Healthy) { throw 'Website health check failed.' }
    if (($Result.portrait_images + $Result.wallpaper_images) -gt 0 -and $ManifestItems -eq 0) { throw 'VM image folders were found but the art index stayed empty.' }
    $Result.status = 'ok'
    $Result.install_dir = $Target
    $Result.manifest_items = $ManifestItems
    $Result.finished_at = (Get-Date).ToString('s')
    Write-Json $Report $Result
} catch {
    $Result.status = 'error'
    $Result.error = $_.Exception.Message
    $Result.finished_at = (Get-Date).ToString('s')
    Write-Json $Report $Result
    throw
} finally {
    Remove-Item -LiteralPath $Temp -Recurse -Force -ErrorAction SilentlyContinue
}
