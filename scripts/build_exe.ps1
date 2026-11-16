# ============================================================================
# Build Standalone Windows 11 Executable Folder Bundle (PyInstaller onedir)
# Note: onedir launches 4x faster than onefile on NVMe SSDs and stays local.
# ============================================================================
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) { $PyExe = "python" }

Write-Host "[*] Installing/verifying PyInstaller..." -ForegroundColor Cyan
& $PyExe -m pip install pyinstaller

Write-Host "[*] Building dist\JarvisPersonalAI\JarvisPersonalAI.exe..." -ForegroundColor Cyan
& $PyExe -m PyInstaller jarvis.spec --noconfirm

Write-Host "[OK] Standalone Windows 11 app built at: dist\JarvisPersonalAI\JarvisPersonalAI.exe" -ForegroundColor Green
