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

# v0.12.2: the .exe (and Start Menu shortcut) icon, drawn by smartcal\ui\appicon.py.
$ico = Join-Path $root "build\SmartCal.ico"
New-Item -ItemType Directory -Force (Split-Path $ico) | Out-Null
python scripts\make_icon.py $ico
if ($LASTEXITCODE -ne 0) { throw "make_icon failed ($LASTEXITCODE)" }

# v0.12.3: bundle the maintainer's OAuth client so end users need no Google Cloud project.
# The file is gitignored (never committed); a build without it still works, but users
# then need their own client_secret.json (see README).
$bundle = @()
$client = Join-Path $root "bundled_client_secret.json"
if (Test-Path $client) { $bundle = @("--add-data", "$client;.") }
else { Write-Warning "bundled_client_secret.json not found: this build has no bundled Google client." }

python -m PyInstaller --noconfirm --clean --windowed --name SmartCal `
    @bundle `
    --icon $ico `
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

# v0.16.0: SmartCal-mcp.exe, the console program Claude Code / Claude Desktop starts to
# talk to SmartCal (a windowed exe has no stdin/stdout). One file, placed next to SmartCal.exe.
python -m PyInstaller --noconfirm --clean --console --onefile --name SmartCal-mcp `
    --specpath build `
    --distpath (Join-Path $dist "SmartCal") `
    --workpath (Join-Path $work "mcp") `
    --exclude-module PyQt6 --exclude-module anthropic `
    smartcal_mcp.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller (mcp) failed ($LASTEXITCODE)" }
$mcp = Join-Path $dist "SmartCal\SmartCal-mcp.exe"
if (-not (Test-Path $mcp)) { throw "PyInstaller finished but $mcp is missing" }
Write-Host "Built: $mcp"
