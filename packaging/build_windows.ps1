# packaging/build_windows.ps1  Builds Zephyr.exe and Zephyr-Windows.zip. Windows only. Owner: Swayam.
#
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
#   (from the repo, with the project venv active)
#
# Output in packaging\dist\:
#   Zephyr\Zephyr.exe     double-click to run
#   Zephyr-Windows.zip    the file to share (GitHub release)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$Py = if ($env:PYTHON) { $env:PYTHON } else { "python" }
& $Py -c "import sys; v = sys.version_info[:2]; sys.exit(0 if v in ((3, 11), (3, 12)) else 'Use Python 3.11 or 3.12 (this is ' + sys.version.split()[0] + '). Activate the project venv first.')"
if ($LASTEXITCODE -ne 0) { exit 1 }

$Out = "packaging\dist"
$Work = "packaging\build"
$AppHome = Join-Path $env:APPDATA "Zephyr"

Write-Host "==> requirements and PyInstaller"
& $Py -m pip install -q -r requirements.txt "pyinstaller>=6.10,<7"
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "==> MediaPipe models"
& $Py tools/get_models.py
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "==> building (a few minutes)"
foreach ($old in @($Work, "$Out\Zephyr", "$Out\Zephyr-Windows.zip")) {
    if (Test-Path $old) { Remove-Item -Recurse -Force $old }
}
& $Py -m PyInstaller --noconfirm --clean --distpath $Out --workpath $Work packaging\Zephyr_win.spec
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "==> zip"
Compress-Archive -Path "$Out\Zephyr" -DestinationPath "$Out\Zephyr-Windows.zip"

# keys for air writing and sync stay on this PC next to the app's data, never inside the app
if ((Test-Path ".env") -and -not (Test-Path "$AppHome\.env")) {
    New-Item -ItemType Directory -Force -Path $AppHome | Out-Null
    Copy-Item ".env" "$AppHome\.env"
    Write-Host "==> copied your .env to $AppHome (stays on this PC, not in the app)"
}

Write-Host ""
Write-Host "Done."
Write-Host "  $Out\Zephyr\Zephyr.exe     double-click it to test"
Write-Host "  $Out\Zephyr-Windows.zip    share this one"
explorer.exe /select,"$((Resolve-Path "$Out\Zephyr-Windows.zip").Path)"
