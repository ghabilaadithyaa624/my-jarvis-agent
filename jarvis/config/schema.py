"""Validated configuration schemas for JARVIS PERSONAL AI.

Model configuration, voice profiles, security boundaries, and hardware budgets
are strictly separated from application logic and validated with Pydantic v2.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Dict, List, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def is_loopback_url(url: str) -> bool:
    """Verify a URL points strictly to the local machine (no cloud leakage)."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").strip().lower()
    if host in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class OllamaConfig(BaseModel):
    """Exclusive local Ollama inference provider configuration."""

    base_url: str = Field(default="http://127.0.0.1:11434")
    preferred_model: str = Field(
        default="",
        description="Empty string auto-selects best installed 6GB-VRAM-safe local model.",
    )
    fallback_models: List[str] = Field(
        default_factory=lambda: [
            "qwen2.5:7b",
            "qwen2.5:7b-instruct",
            "llama3.1:8b",
            "qwen2.5:3b",
            "llama3.2:3b",
            "mistral:7b",
            "phi3:mini",
            "gemma2:2b",
        ]
    )
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    top_p: float = Field(default=0.9, ge=0.0, le=1.0)
    num_ctx: int = Field(
        default=4096,
        ge=1024,
        le=32768,
        description="4096 keeps KV cache ~350-500MB on RTX 3050 6GB VRAM.",
    )
    low_vram_num_ctx: int = Field(default=2048, ge=1024, le=8192)
    max_output_tokens: int = Field(default=1024, ge=128, le=8192)
    keep_alive: str = Field(
        default="5m",
        description="Unloads idle LLM from RTX 3050 VRAM after 5 minutes.",
    )
    unload_previous_on_switch: bool = Field(
        default=True,
        description="Forces keep_alive=0 on prior model before loading a new one.",
    )
    connect_timeout_sec: float = Field(default=5.0, ge=1.0, le=60.0)
    read_timeout_sec: float = Field(default=90.0, ge=5.0, le=600.0)
    max_retries: int = Field(default=2, ge=0, le=5)
    enforce_local_only: bool = Field(
        default=True,
        description="Hard security invariant: rejects any non-loopback endpoint.",
    )

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        if not is_loopback_url(cleaned):
            raise ValueError(
                f"Ollama base_url must be a local loopback address (got '{value}'). "
                "Cloud or remote endpoints are strictly forbidden."
            )
        return cleaned


class LanguageProfile(BaseModel):
    """Per-language speech recognition, wake-word, and voice synthesis profile."""

    code: str
    display_name: str
    whisper_code: str
    wake_phrases: List[str]
    piper_voice: str
    piper_model_path: str = ""
    sapi_voice_hint: str = ""
    system_prompt_hint: str = ""


class STTConfig(BaseModel):
    """Local Speech-to-Text and VAD configuration."""

    backend: Literal["faster_whisper", "whisper_cpp"] = "faster_whisper"
    model_size: str = Field(
        default="small",
        description="faster-whisper 'small' int8 on i5-13420H CPU uses 0MB VRAM and ~420MB RAM.",
    )
    device: Literal["cpu", "cuda", "auto"] = Field(
        default="cpu",
        description="Default CPU preserves all 6GB RTX 3050 VRAM for Ollama.",
    )
    compute_type: str = "int8"
    whisper_cpp_binary: str = ""
    whisper_cpp_model: str = ""
    sample_rate: int = 16000
    listen_mode: Literal["wake_word", "continuous", "push_to_talk"] = "wake_word"
    vad_energy_threshold_db: float = Field(default=-36.0, ge=-65.0, le=-10.0)
    vad_silence_duration_sec: float = Field(default=0.85, ge=0.3, le=3.0)
    vad_min_speech_sec: float = Field(default=0.30, ge=0.1, le=2.0)
    max_utterance_sec: float = Field(default=25.0, ge=5.0, le=90.0)
    followup_window_sec: float = Field(
        default=8.0,
        ge=0.0,
        le=30.0,
        description="Seconds after Jarvis finishes speaking to accept follow-up without repeating wake word.",
    )
    barge_in_enabled: bool = True
    use_openwakeword: bool = True
    openwakeword_model: str = "hey_jarvis"
    openwakeword_threshold: float = Field(default=0.55, ge=0.1, le=0.98)
    idle_unload_sec: int = Field(default=600, ge=0, le=7200)


class TTSConfig(BaseModel):
    """Local offline Text-to-Speech configuration."""

    backend: Literal["auto", "piper", "sapi5", "pyttsx3", "disabled"] = "auto"
    piper_binary: str = ""
    voices_dir: str = ""
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    volume: float = Field(default=0.85, ge=0.0, le=1.0)
    muted: bool = False
    max_spoken_chars: int = Field(default=650, ge=120, le=3000)
    announce_code_omission: bool = True


class ResourceConfig(BaseModel):
    """Hardware budget limits tuned for RTX 3050 6GB VRAM + 16GB RAM."""

    gpu_vram_total_mb: int = 6144
    gpu_vram_reserve_os_mb: int = 900
    gpu_vram_min_free_for_cuda_stt_mb: int = 2000
    ram_total_mb: int = 16384
    ram_warn_percent: float = 85.0
    cpu_warn_percent: float = 90.0
    telemetry_interval_sec: float = Field(default=3.0, ge=1.0, le=30.0)
    cpu_threads_stt: int = Field(
        default=4,
        ge=1,
        le=16,
        description="4 threads matches i5-13420H performance cores cleanly.",
    )


class AgentConfig(BaseModel):
    """Autonomous Execution Engine bounds and safety timeouts."""

    max_steps_per_task: int = Field(default=8, ge=1, le=20)
    max_retries_per_step: int = Field(default=2, ge=0, le=5)
    step_timeout_sec: float = Field(default=45.0, ge=5.0, le=300.0)
    task_timeout_sec: float = Field(default=180.0, ge=15.0, le=900.0)
    max_history_messages: int = Field(default=12, ge=2, le=50)
    enable_fast_path: bool = Field(
        default=True,
        description="Sub-50ms deterministic dispatch for unambiguous single-tool commands.",
    )
    active_language: str = "en"


class SecurityConfig(BaseModel):
    """Explicit security boundaries, allowlists, and confirmation policy."""

    permitted_directories: List[str] = Field(default_factory=list)
    permitted_drives: List[str] = Field(default_factory=lambda: ["C:", "D:"])
    allowlisted_apps: List[str] = Field(
        default_factory=lambda: [
            "code",
            "vscode",
            "blender",
            "notepad",
            "calc",
            "calculator",
            "explorer",
            "mspaint",
            "chrome",
            "msedge",
            "edge",
            "firefox",
            "brave",
            "wt",
            "terminal",
            "powershell",
            "cmd",
            "git",
            "python",
            "vlc",
            "obs",
            "spotify",
            "discord",
            "taskmgr",
            "snippingtool",
            "word",
            "excel",
            "powerpoint",
        ]
    )
    approved_terminal_prefixes: List[str] = Field(
        default_factory=lambda: [
            "python",
            "python3",
            "py",
            "pytest",
            "pip list",
            "pip show",
            "pip check",
            "git status",
            "git log",
            "git diff",
            "git branch",
            "node --version",
            "npm --version",
            "npm test",
            "npm run",
            "ollama list",
            "ollama ps",
            "ollama --version",
            "dir",
            "ls",
            "echo",
            "whoami",
            "hostname",
            "ipconfig",
            "systeminfo",
            "nvidia-smi",
            "where",
            "which",
            "type",
            "Get-ChildItem",
            "Get-Process",
            "Get-Date",
        ]
    )
    require_confirmation_for_delete: bool = True
    require_confirmation_for_overwrite: bool = True
    require_confirmation_for_install: bool = True
    require_confirmation_for_admin: bool = True
    require_confirmation_for_process_stop: bool = True
    require_confirmation_for_unlisted_app: bool = True
    require_confirmation_for_sensitive_web: bool = True
    confirmation_timeout_sec: float = Field(default=60.0, ge=10.0, le=300.0)
    max_file_read_bytes: int = Field(default=2_000_000, ge=10_000, le=50_000_000)
    max_search_results: int = Field(default=100, ge=10, le=1000)


class BrowserConfig(BaseModel):
    """Browser & Playwright automation settings."""

    default_search_engine: Literal["duckduckgo", "google", "bing", "youtube", "github"] = "duckduckgo"
    playwright_headless: bool = False
    playwright_browser: Literal["chromium", "firefox", "webkit"] = "chromium"
    navigation_timeout_ms: int = Field(default=20000, ge=3000, le=120000)


class GUIConfig(BaseModel):
    """PySide6 Desktop HUD settings."""

    window_title: str = "JARVIS PERSONAL AI — Local Desktop Operator"
    window_width: int = 1360
    window_height: int = 840
    minimize_to_tray: bool = True
    orb_fps: int = Field(default=24, ge=10, le=60)
    show_thoughts: bool = True


class JarvisConfig(BaseModel):
    """Root application configuration."""

    data_dir: str = Field(
        default="",
        description="Local persistent storage directory (defaults to ~/.jarvis).",
    )
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    languages: Dict[str, LanguageProfile] = Field(
        default_factory=lambda: {
            "en": LanguageProfile(
                code="en",
                display_name="English",
                whisper_code="en",
                wake_phrases=["hey jarvis", "ok jarvis", "okay jarvis", "jarvis"],
                piper_voice="en_GB-alan-medium",
                sapi_voice_hint="David|Zira|Hazel|George|English",
                system_prompt_hint="Respond clearly and concisely in natural English.",
            ),
            "ta": LanguageProfile(
                code="ta",
                display_name="Tamil (தமிழ்)",
                whisper_code="ta",
                wake_phrases=["ஜார்விஸ்", "ஹே ஜார்விஸ்", "hey jarvis", "jarvis"],
                piper_voice="ta_IN-valluvar-medium",
                sapi_voice_hint="Valluvar|Heera|Tamil",
                system_prompt_hint=(
                    "Respond naturally in Tamil (தமிழ்), keeping technical terms "
                    "(like VS Code, Python, Blender, CPU, RAM) clear and natural."
                ),
            ),
        }
    )
    resources: ResourceConfig = Field(default_factory=ResourceConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    gui: GUIConfig = Field(default_factory=GUIConfig)

    def resolved_data_dir(self) -> Path:
        if self.data_dir:
            return Path(self.data_dir).expanduser().resolve()
        return (Path.home() / ".jarvis").resolve()

    def active_language_profile(self) -> LanguageProfile:
        code = self.agent.active_language
        if code in self.languages:
            return self.languages[code]
        return next(iter(self.languages.values()))
