"""Comprehensive Environment Inspector & Dependency Doctor (Phase 1).

Inspects OS, CPU, GPU/VRAM, RAM, NVMe storage, Python runtime, optional packages,
local Ollama server & models, voice binaries/devices, and desktop apps without
assuming any dependency is pre-installed.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import platform
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from jarvis.config.schema import JarvisConfig
from jarvis.models.model_manager import ModelManager
from jarvis.system.monitor import SystemMonitor


OPTIONAL_PACKAGES: List[Dict[str, str]] = [
    {"import": "httpx", "pip": "httpx", "role": "Ollama local HTTP streaming client", "required": "yes"},
    {"import": "pydantic", "pip": "pydantic", "role": "Config & tool schema validation", "required": "yes"},
    {"import": "psutil", "pip": "psutil", "role": "CPU/RAM/disk/process telemetry", "required": "yes"},
    {"import": "yaml", "pip": "PyYAML", "role": "YAML configuration loader", "required": "yes"},
    {"import": "numpy", "pip": "numpy", "role": "VAD audio signal processing", "required": "yes"},
    {"import": "PySide6", "pip": "PySide6", "role": "JARVIS dark desktop HUD GUI", "required": "recommended"},
    {"import": "faster_whisper", "pip": "faster-whisper", "role": "Offline local speech-to-text", "required": "recommended"},
    {"import": "sounddevice", "pip": "sounddevice", "role": "Microphone & speaker audio I/O", "required": "recommended"},
    {"import": "openwakeword", "pip": "openwakeword", "role": "Offline 'Hey Jarvis' wake-word engine", "required": "optional"},
    {"import": "piper", "pip": "piper-tts", "role": "Neural offline TTS (English & Tamil)", "required": "recommended"},
    {"import": "pyttsx3", "pip": "pyttsx3", "role": "Windows SAPI5 offline TTS fallback", "required": "optional"},
    {"import": "pycaw", "pip": "pycaw", "role": "Windows 11 WASAPI master volume control", "required": "windows"},
    {"import": "mss", "pip": "mss", "role": "Fast multi-monitor screenshot capture", "required": "optional"},
    {"import": "PIL", "pip": "Pillow", "role": "Image capture & verification", "required": "optional"},
    {"import": "send2trash", "pip": "Send2Trash", "role": "Safe Recycle Bin deletion", "required": "recommended"},
    {"import": "docx", "pip": "python-docx", "role": "Read Word (.docx) documents", "required": "optional"},
    {"import": "pypdf", "pip": "pypdf", "role": "Read PDF documents", "required": "optional"},
    {"import": "openpyxl", "pip": "openpyxl", "role": "Read Excel (.xlsx) spreadsheets", "required": "optional"},
    {"import": "playwright", "pip": "playwright", "role": "Interactive browser automation", "required": "optional"},
]


def _check_package(import_name: str, pip_name: str) -> Dict[str, Any]:
    spec = importlib.util.find_spec(import_name)
    if spec is None:
        return {"installed": False, "version": None}
    version = "installed"
    for candidate in (pip_name, import_name):
        try:
            version = importlib.metadata.version(candidate)
            break
        except importlib.metadata.PackageNotFoundError:
            continue
    return {"installed": True, "version": version}


def _check_audio_devices() -> Dict[str, Any]:
    if importlib.util.find_spec("sounddevice") is None:
        return {"probed": False, "inputs": [], "outputs": [], "note": "sounddevice not installed"}
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        inputs = [d["name"] for d in devices if d.get("max_input_channels", 0) > 0]
        outputs = [d["name"] for d in devices if d.get("max_output_channels", 0) > 0]
        return {"probed": True, "inputs": inputs[:10], "outputs": outputs[:10], "note": "ok"}
    except Exception as exc:
        return {"probed": False, "inputs": [], "outputs": [], "note": str(exc)}


def _check_piper_assets(config: JarvisConfig) -> Dict[str, Any]:
    binary = config.tts.piper_binary or shutil.which("piper") or shutil.which("piper.exe") or ""
    voices_dir = Path(config.tts.voices_dir or (config.resolved_data_dir() / "voices"))
    onnx_files = [p.name for p in voices_dir.glob("*.onnx")] if voices_dir.exists() else []
    py_module = importlib.util.find_spec("piper") is not None
    return {
        "piper_binary": binary or None,
        "piper_python_module": py_module,
        "voices_dir": str(voices_dir),
        "installed_voice_models": onnx_files,
        "sapi5_available": os.name == "nt",
    }


def _check_common_binaries() -> Dict[str, Optional[str]]:
    probes = {
        "ollama": ["ollama"],
        "vscode": ["code", "code.cmd"],
        "blender": ["blender"],
        "git": ["git"],
        "node": ["node"],
        "ffmpeg": ["ffmpeg"],
    }
    found: Dict[str, Optional[str]] = {}
    for label, names in probes.items():
        path = None
        for n in names:
            path = shutil.which(n)
            if path:
                break
        if not path and os.name == "nt":
            if label == "vscode":
                cand = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code" / "bin" / "code.cmd"
                if cand.exists():
                    path = str(cand)
            elif label == "blender":
                bf = Path(r"C:\Program Files\Blender Foundation")
                if bf.exists():
                    exes = sorted(bf.glob("Blender*/blender.exe"), reverse=True)
                    if exes:
                        path = str(exes[0])
            elif label == "ollama":
                cand = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
                if cand.exists():
                    path = str(cand)
        found[label] = path
    return found


def inspect_environment(
    config: JarvisConfig,
    model_manager: Optional[ModelManager] = None,
    monitor: Optional[SystemMonitor] = None,
) -> Dict[str, Any]:
    """Run full non-destructive environment readiness inspection."""
    mon = monitor or SystemMonitor()
    specs = mon.get_system_specs()

    pkg_report: Dict[str, Dict[str, Any]] = {}
    missing_required: List[str] = []
    missing_recommended: List[str] = []

    for item in OPTIONAL_PACKAGES:
        if item["required"] == "windows" and os.name != "nt":
            pkg_report[item["pip"]] = {
                "installed": False,
                "version": None,
                "role": item["role"],
                "status": "skipped_non_windows",
            }
            continue
        status = _check_package(item["import"], item["pip"])
        pkg_report[item["pip"]] = {
            **status,
            "role": item["role"],
            "required": item["required"],
        }
        if not status["installed"]:
            if item["required"] == "yes":
                missing_required.append(item["pip"])
            elif item["required"] in {"recommended", "windows"}:
                missing_recommended.append(item["pip"])

    mm = model_manager or ModelManager(config.ollama, config.resources)
    mm.refresh_models()
    ollama_status = mm.get_status_snapshot()

    audio = _check_audio_devices()
    piper_info = _check_piper_assets(config)
    binaries = _check_common_binaries()

    action_items: List[str] = []
    if platform.system() != "Windows":
        action_items.append(
            f"Running on {platform.system()} ({platform.release()}). Core agent, Ollama, filesystem, "
            "memory, security & GUI work; Win32-exclusive APIs (WASAPI volume, ms-settings, window focus) "
            "activate automatically on Windows 11."
        )
    if not ollama_status["online"]:
        action_items.append(
            "Start local Ollama ('ollama serve' or open Ollama on Windows 11) and verify http://127.0.0.1:11434."
        )
    elif not ollama_status["compatible_models"]:
        action_items.append(
            "Pull a 6GB-VRAM-optimized local model: ollama pull qwen2.5:7b (and optionally ollama pull qwen2.5:3b)."
        )
    if missing_required:
        action_items.append(f"Install missing required packages: pip install {' '.join(missing_required)}")
    if missing_recommended:
        action_items.append(f"Install recommended desktop/voice packages: pip install {' '.join(missing_recommended)}")
    if not piper_info["installed_voice_models"]:
        action_items.append(
            f"Download an offline Piper .onnx voice into {piper_info['voices_dir']} "
            "(e.g., en_GB-alan-medium.onnx for English, ta_IN-valluvar-medium.onnx for Tamil). "
            "Windows SAPI5 offline TTS will serve as automatic fallback until then."
        )

    return {
        "is_windows_11": os.name == "nt" and sys_is_win11(specs.get("os_version", "")),
        "hardware_and_os": specs,
        "ollama": ollama_status,
        "packages": pkg_report,
        "missing_required_packages": missing_required,
        "missing_recommended_packages": missing_recommended,
        "audio_devices": audio,
        "tts_assets": piper_info,
        "external_binaries": binaries,
        "permitted_directories": list(config.security.permitted_directories),
        "data_dir": str(config.resolved_data_dir()),
        "action_items": action_items,
    }


def sys_is_win11(version_str: str) -> bool:
    """Windows 11 reports NT 10.0 with build >= 22000."""
    try:
        parts = [int(p) for p in version_str.split(".") if p.isdigit()]
        return len(parts) >= 3 and parts[2] >= 22000
    except Exception:
        return False
