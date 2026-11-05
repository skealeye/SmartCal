# Copy dist\SmartCal to %LOCALAPPDATA%\Programs\SmartCal and add a Start Menu shortcut.
# Does not touch the registry: SmartCal registers its own autostart on first run.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $root "dist\SmartCal"
if (-not (Test-Path (Join-Path $src "SmartCal.exe"))) { throw "Run scripts\build.ps1 first." }

$dest = Join-Path $env:LOCALAPPDATA "Programs\SmartCal"
New-Item -ItemType Directory -Force $dest | Out-Null
Copy-Item -Path (Join-Path $src "*") -Destination $dest -Recurse -Force

$lnk = Join-Path ([Environment]::GetFolderPath("Programs")) "SmartCal.lnk"
$sh = New-Object -ComObject WScript.Shell
$s = $sh.CreateShortcut($lnk)
$s.TargetPath = Join-Path $dest "SmartCal.exe"
$s.WorkingDirectory = $dest
$s.Save()
Write-Host "Installed to $dest; shortcut: $lnk"
