# Build SmartCal as a one-folder, windowed PyInstaller app: dist\SmartCal\SmartCal.exe
# Run from anywhere; uses the active Python. Needs: pip install -r requirements-dev.txt
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -m PyInstaller --noconfirm --clean --windowed --name SmartCal `
    --specpath build `
    --collect-data googleapiclient `
    --collect-data httplib2 `
    --collect-submodules winotify `
    --hidden-import winotify `
    run.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }
Write-Host "Built: $root\dist\SmartCal\SmartCal.exe"
