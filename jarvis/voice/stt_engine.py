"""Local Speech-to-Text Engine (faster-whisper primary + whisper.cpp CLI backend).

Defaults to CPU int8 quantization on Intel Core i5-13420H (0 MB GPU VRAM),
with automatic CPU fallback if CUDA fails or VRAM is constrained.
Supports English ('en'), Tamil ('ta'), and automatic language detection.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

from jarvis.config.schema import ResourceConfig, STTConfig
from jarvis.logs.logger import get_logger

log = get_logger("voice.stt")


@dataclass
class TranscriptionResult:
    text: str
    language: str
    confidence: float
    duration_ms: float
    backend: str
    error: Optional[str] = None


class SpeechToTextEngine:
    """Lazy-loaded local offline Speech-to-Text engine with idle memory unloading."""

    def __init__(
        self,
        stt_cfg: STTConfig,
        resource_cfg: Optional[ResourceConfig] = None,
        device_resolver: Optional[callable] = None,
    ) -> None:
        self.cfg = stt_cfg
        self.resource_cfg = resource_cfg or ResourceConfig()
        self._device_resolver = device_resolver
        self._lock = threading.RLock()
        self._fw_model = None
        self._loaded_device = ""
        self._last_used: float = 0.0

    def check_availability(self) -> Tuple[bool, str]:
        if self.cfg.backend == "whisper_cpp":
            binary = self.cfg.whisper_cpp_binary or shutil.which("whisper-cli") or shutil.which("main")
            if binary and self.cfg.whisper_cpp_model and Path(self.cfg.whisper_cpp_model).is_file():
                return True, f"whisper.cpp ready ({binary})"
            return False, "whisper.cpp binary or GGUF model path not configured."

        if importlib.util.find_spec("faster_whisper") is not None:
            return True, f"faster-whisper available (model={self.cfg.model_size}, device={self.cfg.device}, compute={self.cfg.compute_type})"
        return False, "faster-whisper is not installed. Run: pip install faster-whisper sounddevice"

    def _ensure_faster_whisper(self) -> None:
        with self._lock:
            if self._fw_model is not None:
                return
            from faster_whisper import WhisperModel  # type: ignore

            if self._device_resolver is not None:
                device, compute_type = self._device_resolver()
            else:
                device = "cpu" if self.cfg.device in {"cpu", "auto"} else "cuda"
                compute_type = self.cfg.compute_type or "int8"

            try:
                log.info(
                    "Loading faster-whisper model='%s' device='%s' compute='%s' threads=%d",
                    self.cfg.model_size,
                    device,
                    compute_type,
                    self.resource_cfg.cpu_threads_stt,
                )
                self._fw_model = WhisperModel(
                    self.cfg.model_size,
                    device=device,
                    compute_type=compute_type,
                    cpu_threads=self.resource_cfg.cpu_threads_stt,
                )
                self._loaded_device = device
            except Exception as exc:
                if device != "cpu":
                    log.warning("CUDA STT init failed (%s); falling back to CPU int8.", exc)
                    self._fw_model = WhisperModel(
                        self.cfg.model_size,
                        device="cpu",
                        compute_type="int8",
                        cpu_threads=self.resource_cfg.cpu_threads_stt,
                    )
                    self._loaded_device = "cpu"
                else:
                    raise

    def unload_if_idle(self) -> bool:
        """Release STT RAM after prolonged inactivity."""
        if self.cfg.idle_unload_sec <= 0:
            return False
        with self._lock:
            if self._fw_model is not None and (time.time() - self._last_used) >= self.cfg.idle_unload_sec:
                self._fw_model = None
                log.info("Unloaded idle faster-whisper model to conserve system RAM.")
                return True
        return False

    def unload(self) -> None:
        with self._lock:
            self._fw_model = None

    def transcribe(
        self,
        audio_float32: np.ndarray,
        language: Optional[str] = "en",
    ) -> TranscriptionResult:
        """Transcribe a 16kHz mono float32 numpy waveform entirely offline."""
        started = time.perf_counter()
        if audio_float32 is None or audio_float32.size < 1600:
            return TranscriptionResult("", language or "en", 0.0, 0.0, self.cfg.backend)

        if self.cfg.backend == "whisper_cpp":
            return self._transcribe_whisper_cpp(audio_float32, language, started)
        return self._transcribe_faster_whisper(audio_float32, language, started)

    def _transcribe_faster_whisper(
        self,
        audio: np.ndarray,
        language: Optional[str],
        started: float,
    ) -> TranscriptionResult:
        try:
            self._ensure_faster_whisper()
            lang_arg = None if language in {None, "", "auto"} else language
            # Helpful domain prompt boosts recognition of JARVIS, VS Code, Blender, Ollama & Tamil loanwords
            initial_prompt = (
                "Jarvis, VS Code, Blender, Ollama, Python, Playwright, Windows 11, Downloads, CPU, RAM, GPU."
                if lang_arg in {None, "en"}
                else "ஜார்விஸ், VS Code, Blender, Python, Windows."
            )
            with self._lock:
                segments, info = self._fw_model.transcribe(
                    audio.astype(np.float32, copy=False),
                    language=lang_arg,
                    beam_size=3,
                    best_of=1,
                    temperature=0.0,
                    condition_on_previous_text=False,
                    vad_filter=False,  # Already cleanly segmented by AdaptiveVAD
                    initial_prompt=initial_prompt,
                )
                texts = []
                probs = []
                for seg in segments:
                    # Suppress classic Whisper silence hallucinations
                    if getattr(seg, "no_speech_prob", 0.0) > 0.75:
                        continue
                    t = (seg.text or "").strip()
                    if t:
                        texts.append(t)
                        probs.append(max(0.0, min(1.0, float(np.exp(getattr(seg, "avg_logprob", -0.3))))))
                self._last_used = time.time()

            full_text = " ".join(texts).strip()
            # Filter phantom single-phrase Whisper noise artifacts
            if full_text.lower() in {"thank you.", "thanks for watching.", "you", ".", "subtitles by"}:
                full_text = ""

            elapsed = round((time.perf_counter() - started) * 1000.0, 1)
            conf = round(float(np.mean(probs)), 3) if probs else 0.0
            detected_lang = getattr(info, "language", language or "en")
            return TranscriptionResult(
                text=full_text,
                language=detected_lang,
                confidence=conf,
                duration_ms=elapsed,
                backend=f"faster_whisper:{self._loaded_device}",
            )
        except ImportError:
            return TranscriptionResult(
                text="",
                language=language or "en",
                confidence=0.0,
                duration_ms=0.0,
                backend="faster_whisper",
                error="faster-whisper not installed (pip install faster-whisper).",
            )
        except Exception as exc:
            log.error("STT transcription error: %s", exc)
            return TranscriptionResult(
                text="",
                language=language or "en",
                confidence=0.0,
                duration_ms=round((time.perf_counter() - started) * 1000.0, 1),
                backend="faster_whisper",
                error=str(exc),
            )

    def _transcribe_whisper_cpp(
        self,
        audio: np.ndarray,
        language: Optional[str],
        started: float,
    ) -> TranscriptionResult:
        binary = self.cfg.whisper_cpp_binary or shutil.which("whisper-cli") or shutil.which("main")
        if not binary or not self.cfg.whisper_cpp_model:
            return TranscriptionResult(
                "", language or "en", 0.0, 0.0, "whisper_cpp",
                error="Configure stt.whisper_cpp_binary and stt.whisper_cpp_model.",
            )
        tmp_wav = None
        try:
            fd, tmp_wav = tempfile.mkstemp(suffix=".wav", prefix="jarvis_stt_")
            os.close(fd)
            pcm16 = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
            with wave.open(tmp_wav, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.cfg.sample_rate)
                wf.writeframes(pcm16.tobytes())

            cmd = [
                binary,
                "-m", self.cfg.whisper_cpp_model,
                "-f", tmp_wav,
                "-t", str(self.resource_cfg.cpu_threads_stt),
                "-nt",
                "-np",
            ]
            if language and language != "auto":
                cmd.extend(["-l", language])

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30.0,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
            )
            text = proc.stdout.strip()
            elapsed = round((time.perf_counter() - started) * 1000.0, 1)
            if proc.returncode != 0:
                return TranscriptionResult("", language or "en", 0.0, elapsed, "whisper_cpp", error=proc.stderr[:200])
            return TranscriptionResult(text, language or "en", 0.85 if text else 0.0, elapsed, "whisper_cpp")
        except Exception as exc:
            return TranscriptionResult("", language or "en", 0.0, 0.0, "whisper_cpp", error=str(exc))
        finally:
            if tmp_wav and os.path.exists(tmp_wav):
                try:
                    os.remove(tmp_wav)
                except OSError:
                    pass
