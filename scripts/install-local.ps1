# Copy the build (%LOCALAPPDATA%\SmartCal-build\dist\SmartCal, or the older repo
# dist\SmartCal if that is all there is) to %LOCALAPPDATA%\Programs\SmartCal and add
# a Start Menu shortcut.
# Does not touch the registry: SmartCal registers its own autostart on first run.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $env:LOCALAPPDATA "SmartCal-build\dist\SmartCal"
if (-not (Test-Path (Join-Path $src "SmartCal.exe"))) {
    $src = Join-Path $root "dist\SmartCal"   # built by an older build.ps1
}
if (-not (Test-Path (Join-Path $src "SmartCal.exe"))) { throw "Run scripts\build.ps1 first." }
Write-Host "Installing from $src"

if (Get-Process SmartCal -ErrorAction SilentlyContinue) {
    Write-Host "Close SmartCal (right-click the tray icon > Quit) and run this again."
    exit 1
}
$dest = Join-Path $env:LOCALAPPDATA "Programs\SmartCal"
New-Item -ItemType Directory -Force $dest | Out-Null
Copy-Item -Path (Join-Path $src "*") -Destination $dest -Recurse -Force

$lnk = Join-Path ([Environment]::GetFolderPath("Programs")) "SmartCal.lnk"
$sh = New-Object -ComObject WScript.Shell
$s = $sh.CreateShortcut($lnk)
$s.TargetPath = Join-Path $dest "SmartCal.exe"
$s.WorkingDirectory = $dest
$s.IconLocation = (Join-Path $dest "SmartCal.exe") + ",0"   # v0.12.2: the app icon
$s.Save()
Write-Host "Installed to $dest; shortcut: $lnk"
