# Build SmartCal as a one-folder, windowed PyInstaller app:
#   %LOCALAPPDATA%\SmartCal-build\dist\SmartCal\SmartCal.exe
# The output and work folders are on the local disk: building into the repo on the
# Z: network share fails intermittently (NAS delete-pending errors). The .spec file
# stays in the repo's build\ folder (PyInstaller cannot make relative paths across
# drives, so the spec must sit on the same drive as run.py).
# Run from anywhere; uses the active Python. Needs: pip install -r requirements-dev.txt
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$out = Join-Path $env:LOCALAPPDATA "SmartCal-build"
$dist = Join-Path $out "dist"
$work = Join-Path $out "work"

python -m PyInstaller --noconfirm --clean --windowed --name SmartCal `
    --specpath build `
    --distpath $dist `
    --workpath $work `
    --collect-data googleapiclient `
    --collect-data httplib2 `
    --hidden-import winotify `
    run.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }
$exe = Join-Path $dist "SmartCal\SmartCal.exe"
if (-not (Test-Path $exe)) { throw "PyInstaller finished but $exe is missing" }
Write-Host "Built: $exe"
