# ============================================================================
# JARVIS PERSONAL AI — Non-Destructive Windows 11 Setup Script
# Target: Intel Core i5-13420H | NVIDIA RTX 3050 6GB VRAM | 16GB RAM | NVMe
# ============================================================================
param(
    # Kept for backwards compatibility: the voice stack is part of the `full` extra,
    # so it is always installed. Use the download switches to fetch Piper models.
    [switch]$IncludeVoice = $true,
    # Also download the Chromium binary used by Playwright automation (~150 MB).
    [switch]$IncludePlaywright = $false,
    [switch]$DownloadEnglishVoice = $false,
    [switch]$DownloadTamilVoice = $false
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "  JARVIS PERSONAL AI — Windows 11 Environment & Setup Wizard" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan

# 1. Verify Python 3.10+
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] Python is not on PATH. Install Python 3.11+ from python.org first." -ForegroundColor Red
    exit 1
}
$pyVer = python --version
Write-Host "[OK] Detected $pyVer" -ForegroundColor Green

# 2. Check NVIDIA RTX GPU via nvidia-smi (0 MB VRAM impact)
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    $gpuLine = nvidia-smi --query-gpu=name,memory.total,memory.free,driver_version --format=csv,noheader
    Write-Host "[OK] Detected GPU: $gpuLine" -ForegroundColor Green
} else {
    Write-Host "[WARN] nvidia-smi not found — JARVIS will run cleanly with CPU fallback." -ForegroundColor Yellow
}

# 3. Check Local Ollama Installation & Models
if (Get-Command ollama -ErrorAction SilentlyContinue) {
    $olVer = ollama --version
    Write-Host "[OK] Detected local Ollama ($olVer)" -ForegroundColor Green
    Write-Host "     Installed local models:"
    ollama list
    Write-Host "     Tip (6GB VRAM sweet spot): ollama pull qwen2.5:7b   (and ollama pull qwen2.5:3b for use alongside Blender)" -ForegroundColor DarkCyan
} else {
    Write-Host "[WARN] Ollama CLI not detected. Install Ollama locally from https://ollama.com/download/windows" -ForegroundColor Yellow
}

# 4. Create isolated virtual environment (.venv) without overwriting user files
$VenvDir = Join-Path $ProjectRoot ".venv"
if (-not (Test-Path $VenvDir)) {
    Write-Host "`n[*] Creating isolated virtual environment (.venv)..." -ForegroundColor Cyan
    python -m venv $VenvDir
} else {
    Write-Host "`n[OK] Existing .venv found — reusing safely." -ForegroundColor Green
}

$PipExe = Join-Path $VenvDir "Scripts\pip.exe"
$PyExe  = Join-Path $VenvDir "Scripts\python.exe"

Write-Host "[*] Upgrading pip and installing JARVIS with the full Windows 11 stack..." -ForegroundColor Cyan
Write-Host "     extras: full = gui + voice + windows + documents + browser, plus dev tooling" -ForegroundColor DarkCyan
& $PyExe -m pip install --upgrade pip
& $PipExe install -e ".[full,dev]"

if (-not $IncludeVoice) {
    Write-Host "[WARN] -IncludeVoice is deprecated: the voice stack ships in the 'full' extra and is always installed." -ForegroundColor Yellow
}

if ($IncludePlaywright) {
    Write-Host "[*] Downloading the local Chromium binary used by Playwright..." -ForegroundColor Cyan
    & $PyExe -m playwright install chromium
}
else {
    Write-Host "[OK] Playwright Python package installed. To enable interactive browser automation run:" -ForegroundColor DarkCyan
    Write-Host "     .venv\Scripts\python -m playwright install chromium" -ForegroundColor DarkCyan
}

# 5. Optional One-Time Offline Piper Neural Voice Downloads (never overwrites existing models)
$VoicesDir = Join-Path $HOME ".jarvis\voices"
New-Item -ItemType Directory -Force -Path $VoicesDir | Out-Null

function Get-VoiceFile($Url, $OutFile) {
    if (Test-Path $OutFile) {
        Write-Host "[OK] Voice file already exists (preserved): $OutFile" -ForegroundColor Green
        return
    }
    Write-Host "[*] Downloading $(Split-Path -Leaf $OutFile)..." -ForegroundColor Cyan
    Invoke-WebRequest -Uri $Url -OutFile $OutFile
}

if ($DownloadEnglishVoice) {
    $base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium"
    Get-VoiceFile "$base/en_GB-alan-medium.onnx" (Join-Path $VoicesDir "en_GB-alan-medium.onnx")
    Get-VoiceFile "$base/en_GB-alan-medium.onnx.json" (Join-Path $VoicesDir "en_GB-alan-medium.onnx.json")
}

if ($DownloadTamilVoice) {
    $baseTa = "https://huggingface.co/rhasspy/piper-voices/resolve/main/ta/ta_IN/valluvar/medium"
    Get-VoiceFile "$baseTa/ta_IN-valluvar-medium.onnx" (Join-Path $VoicesDir "ta_IN-valluvar-medium.onnx")
    Get-VoiceFile "$baseTa/ta_IN-valluvar-medium.onnx.json" (Join-Path $VoicesDir "ta_IN-valluvar-medium.onnx.json")
}

# 6. Run Environment Doctor & Runtime Self-Test
Write-Host "`n[*] Running JARVIS Environment Doctor..." -ForegroundColor Cyan
& $PyExe -m jarvis --doctor

Write-Host "`n[*] Running JARVIS Runtime Verification Self-Test..." -ForegroundColor Cyan
& $PyExe -m jarvis --self-test

Write-Host "`n[DONE] Launch JARVIS anytime with: scripts\run_jarvis.bat  or  .venv\Scripts\python -m jarvis" -ForegroundColor Green
