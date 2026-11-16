# JARVIS PERSONAL AI

[![CI](https://github.com/ghabilaadithyaa624/my-jarvis-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/ghabilaadithyaa624/my-jarvis-agent/actions/workflows/ci.yml)

A complete, privacy-first, **100% local personal AI voice operator and desktop agent** engineered for **Windows 11** and tuned specifically for **Intel Core i5-13420H + NVIDIA RTX 3050 (6 GB VRAM) + 16 GB RAM + 512 GB NVMe SSD**.

JARVIS connects exclusively to your local **Ollama** runtime (`127.0.0.1:11434`), listens offline via **faster-whisper** + **AdaptiveVAD** + **openWakeWord**, speaks offline via **Piper Neural TTS** (with automatic Windows 11 **SAPI5** fallback), supports **English** and **Tamil (தமிழ்)**, enforces strict permission & confirmation gates with a tamper-evident **SHA-256 audit log**, and executes **41 verified desktop, file, system, browser, developer, Blender, and personal utility tools** through a modern **PySide6** dark HUD.

---

## Quick Start on Windows 11

### 1. Prerequisites
- **Windows 11** with **Python 3.10+** installed (`python --version`)
- **Ollama for Windows** installed and running locally (`ollama --version`)
- At least one local chat model pulled for your RTX 3050 6 GB GPU:
  ```powershell
  # Primary operator model (~4.7 GB — fits cleanly in 6 GB VRAM)
  ollama pull qwen2.5:7b

  # Optional ultra-fast companion model (~1.9 GB — ideal when Blender is open)
  ollama pull qwen2.5:3b
  ```

### 2. Install
JARVIS ships as a **small core** plus optional feature extras, so you only pay for what you use:

```powershell
# A) Core only (terminal operator + Ollama + security + tools + memory).
#    ~5 packages, no GUI, no microphone, works on any OS.
pip install -e .

# B) Full Windows 11 install (recommended for the desktop HUD + voice):
pip install -e ".[full]"     # = gui + voice + windows + documents + browser
python -m playwright install chromium   # optional: interactive browser automation
```

### 3. Automated Non-Destructive Setup (Windows 11)
Or let the setup wizard create the virtual environment and install everything for you:
```powershell
# Full stack + offline English Piper neural voice
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 -DownloadEnglishVoice

# Also download the offline Tamil neural voice (ta_IN-valluvar-medium) and Playwright Chromium:
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 -IncludePlaywright -DownloadEnglishVoice -DownloadTamilVoice
```
*(The wizard runs `pip install -e ".[full,dev]"` inside `.venv`. After this one-time installation and model download, JARVIS operates 100% offline with zero cloud dependencies.)*

> Missing an optional package later? JARVIS never shows a traceback — it tells you which extra to install
> (for example `pip install -e ".[gui]"`), and `python -m jarvis --doctor` lists exactly what is present.

### 4. Verify Your Environment
```powershell
.\.venv\Scripts\python -m jarvis --doctor
.\.venv\Scripts\python -m jarvis --self-test
.\.venv\Scripts\pytest -v jarvis/tests
```

### 5. Launch JARVIS
```powershell
# Launch the PySide6 Dark Desktop HUD (console attached)
scripts\run_jarvis.bat

# Or launch silently with zero console window (double-click in Explorer)
wscript.exe scripts\run_jarvis_silent.vbs

# Or run interactive terminal CLI mode
.\.venv\Scripts\python -m jarvis --cli

# Or run a single verified command directly
.\.venv\Scripts\python -m jarvis --once "Jarvis, check my laptop performance."
```

---

## Example Voice & Text Instructions

- *"Jarvis, open VS Code."*
- *"Jarvis, find my Blender project."*
- *"Jarvis, open my Blender educational project and check whether everything is working."*
- *"Jarvis, check my laptop performance."*
- *"Jarvis, explain what is currently running on my computer."*
- *"Jarvis, stop all unnecessary background processes."* *(scans safe candidates first and asks confirmation before terminating any process)*
- *"Jarvis, organize my Downloads folder."* *(sorts into categorized subfolders, never overwrites existing files, and saves a reversible JSON manifest)*
- *"Jarvis, find duplicate files in my Downloads folder."*
- *"Jarvis, create a Python script that calculates prime numbers and execute it."*
- *"Jarvis, set the volume to 45%."* / *"Jarvis, open Bluetooth settings."* / *"Jarvis, take a screenshot."*
- *"Jarvis, set a timer for 10 minutes called tea."* / *"Jarvis, remind me in 30 minutes to review render output."*
- *"ஜார்விஸ், நேரம் என்ன?"* *(Tamil wake word + language switching supported in Settings or via `--lang ta`)*

---

## Voice Engine (English & Tamil)

- **Listening Modes**: `wake_word` (*"Jarvis"* / *"Hey Jarvis"* / *"ஜார்விஸ்"* + 8s follow-up window), `continuous` (hands-free VAD), and `push_to_talk` (hold button in GUI).
- **VRAM Preservation**: `faster-whisper` runs in `int8` mode on 4 threads of your i5-13420H CPU by default (**0 MB GPU VRAM**), keeping all 6 GB VRAM dedicated to Ollama.
- **Speech Output**: Never reads fenced code blocks, stack traces, JSON dumps, URLs, or long paths aloud. Supports instant interruption (voice barge-in, Mute, Cancel Task, or Emergency Stop).
- **Offline Voice Directory**: Place any Piper `.onnx` + `.onnx.json` pair inside `%USERPROFILE%\.jarvis\voices\`:
  - English default: `en_GB-alan-medium.onnx` (or `en_US-lessac-medium.onnx`)
  - Tamil default: `ta_IN-valluvar-medium.onnx`
  - Automatic fallback: Built-in offline Windows 11 SAPI5 works immediately even before downloading Piper voices.

---

## Security & Safety Controls

- **Local Data Root**: All configuration, SQLite memory, voices, workspace scripts, screenshots, and audit logs live strictly on your machine at `%USERPROFILE%\.jarvis\` (override with `--data-dir` or `JARVIS_DATA_DIR`).
- **Permitted Directories & App Allowlist**: Managed live in the GUI **Permissions & Audit** tab or `%USERPROFILE%\.jarvis\config.yaml`.
- **Mandatory Confirmation Prompts**: Required before deleting files (sent to Recycle Bin when `Send2Trash` is installed), overwriting existing files, installing packages, escalating to Administrator, stopping processes, launching unlisted apps, or interacting with financial/messaging web pages.
- **Emergency Stop**: Click the red **EMERGENCY STOP** button (or say *"Emergency stop"*) at any moment to immediately abort LLM streams, cancel active tasks, terminate child subprocesses, stop audio, cancel timers, and close Playwright.

---

## Optional Windows 11 Startup & `.exe` Packaging

```powershell
# Register least-privilege Windows 11 logon startup task
powershell -ExecutionPolicy Bypass -File scripts\install_startup.ps1

# Remove startup task anytime
powershell -ExecutionPolicy Bypass -File scripts\install_startup.ps1 -Uninstall

# Build standalone folder executable (dist\JarvisPersonalAI\JarvisPersonalAI.exe)
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
```

---

## Project Structure

```text
jarvis/
  __init__.py          # Package metadata
  __main__.py          # CLI/GUI launcher (--doctor, --self-test, --cli, --once)
  app/                 # JarvisController orchestrator, EventBus & CLI console
  brain/               # IntentClassifier, TaskPlanner, OutputParser, Verifier & 9-Stage Executor
  models/              # Loopback-only OllamaClient & 6GB-VRAM-aware ModelManager
  voice/               # AdaptiveVAD, WakeWordDetector (EN/TA), faster-whisper/whisper.cpp & Listener
  speech/              # SpeechSanitizer (no code read aloud) & Piper/SAPI5 offline SpeechEngine
  tools/               # Schema-validated ToolRegistry & 41 verified desktop operator tools
  automation/          # Windows 11 AppRegistry, Win32 AppController & Developer/Blender workflows
  browser/             # URL-safe browser launcher & lazy-loaded Playwright controller
  filesystem/          # Verified CRUD/search, .docx/.pdf/.xlsx/.blend reader, Organizer & DuplicateFinder
  system/              # Environment probe, CPU/RAM/GPU Monitor, ResourceGuard & Windows 11 controls
  memory/              # SQLite WAL DB, ConversationStore, FTS5 KnowledgeStore & Personal Utilities
  security/            # SecurityGate, PathGuard, CommandValidator, AppAllowlist & EmergencyStop
  interface/           # PySide6 dark JARVIS HUD, animated VoiceOrbWidget, panels & security dialogs
  config/              # Pydantic schemas, loader, default_config.yaml & separated models_catalog.yaml
  logs/                # Redacting rotating logger & SHA-256 hash-chained AuditLogger
  tests/               # 34 automated unit & end-to-end integration tests across all phases
scripts/
  setup_windows.ps1    # Non-destructive Windows 11 setup wizard
  run_jarvis.bat       # Standard launcher
  run_jarvis_silent.vbs# Windowless GUI launcher
  install_startup.ps1  # Least-privilege Task Scheduler logon registration
  build_exe.ps1        # PyInstaller standalone packaging script
```

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the complete hardware VRAM budget, 9-stage execution pipeline, security model, and verification matrix.
