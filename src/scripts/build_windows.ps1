param(
    [string]$Python = "python",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$venvPath = Join-Path $repoRoot ".venv-build"
if (Test-Path $venvPath) {
    Remove-Item $venvPath -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "[INFO] Creating build virtual environment..."
& $Python -m venv $venvPath

$venvPython = Join-Path $venvPath "Scripts/python.exe"
if (-not (Test-Path $venvPython)) {
    throw "Virtual environment python not found: $venvPython"
}

Write-Host "[INFO] Installing build dependencies..."
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements-dev.txt

if (-not $SkipTests) {
    Write-Host "[INFO] Running tests..."
    & $venvPython -m pytest
}

if (Test-Path "build") {
    Remove-Item "build" -Recurse -Force
}
if (Test-Path "dist") {
    Remove-Item "dist" -Recurse -Force
}

Write-Host "[INFO] Building ForensicPack.exe with PyInstaller..."
& $venvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onedir `
    --name "ForensicPack" `
    --icon "assets/forensicpack_icon.ico" `
    --add-data "assets;assets" `
    forensicpack.py

$exePath = Join-Path $repoRoot "dist/ForensicPack/ForensicPack.exe"
if (-not (Test-Path $exePath)) {
    throw "Build completed but executable was not found at $exePath"
}

Write-Host "[INFO] Build complete: $exePath"
