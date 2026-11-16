"""Interruptible Local Offline Text-to-Speech Engine (Piper Neural TTS + Windows SAPI5).

Zero cloud dependencies. Runs entirely on CPU (0 MB GPU VRAM).
Supports adjustable speaking speed, volume, mute toggle, mid-sentence barge-in
interruption, natural English voices, and extensible Tamil (ta_IN) voices.
"""

from __future__ import annotations

import importlib.util
import io
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import wave
from pathlib import Path
from typing import Callable, Optional, Tuple

import numpy as np

from jarvis.config.schema import LanguageProfile, TTSConfig
from jarvis.logs.logger import get_logger
from jarvis.speech.sanitizer import SpeechSanitizer

log = get_logger("speech.tts")


class SpeechEngine:
    """Asynchronous, interruptible offline speech synthesizer."""

    def __init__(
        self,
        tts_cfg: TTSConfig,
        language_profile: LanguageProfile,
        on_state_change: Optional[Callable[[bool, str], None]] = None,
        on_finished_utterance: Optional[Callable[[], None]] = None,
    ) -> None:
        self.cfg = tts_cfg
        self.language = language_profile
        self.sanitizer = SpeechSanitizer(
            max_chars=tts_cfg.max_spoken_chars,
            announce_code_omission=tts_cfg.announce_code_omission,
        )
        self._on_state = on_state_change
        self._on_finished = on_finished_utterance

        self._queue: "queue.Queue[Optional[Tuple[str, str]]]" = queue.Queue()
        self._interrupt = threading.Event()
        self._shutdown = threading.Event()
        self._speaking = threading.Event()
        self._proc_lock = threading.Lock()
        self._active_proc: Optional[subprocess.Popen] = None
        self._piper_voice_cache: dict[str, object] = {}

        self._worker = threading.Thread(
            target=self._worker_loop,
            name="jarvis-tts-worker",
            daemon=True,
        )
        self._worker.start()

    # ---------------- Properties & Controls ----------------

    @property
    def is_speaking(self) -> bool:
        return self._speaking.is_set()

    @property
    def is_muted(self) -> bool:
        return self.cfg.muted

    def set_muted(self, muted: bool) -> None:
        self.cfg.muted = bool(muted)
        if self.cfg.muted:
            self.stop()

    def set_speed(self, speed: float) -> None:
        self.cfg.speed = max(0.5, min(2.0, float(speed)))

    def set_volume(self, volume: float) -> None:
        self.cfg.volume = max(0.0, min(1.0, float(volume)))

    def set_language(self, profile: LanguageProfile) -> None:
        self.language = profile

    def detect_backend(self) -> Tuple[str, str]:
        """Resolve best available local offline TTS backend without failing."""
        if self.cfg.backend == "disabled":
            return "disabled", "Speech output disabled in settings."

        model_path = self._resolve_piper_model(self.language)
        piper_bin = self._resolve_piper_binary()
        has_piper_py = importlib.util.find_spec("piper") is not None

        if self.cfg.backend in {"auto", "piper"}:
            if model_path and has_piper_py:
                return "piper_python", f"Piper neural TTS ({model_path.name})"
            if model_path and piper_bin:
                return "piper_cli", f"Piper CLI neural TTS ({model_path.name})"
            if self.cfg.backend == "piper":
                return "unavailable", f"Piper voice model '{self.language.piper_voice}.onnx' not found in {self.cfg.voices_dir}."

        if self.cfg.backend in {"auto", "sapi5"} and os.name == "nt":
            return "sapi5", "Windows 11 offline SAPI5 synthesizer"

        if self.cfg.backend in {"auto", "pyttsx3"} and importlib.util.find_spec("pyttsx3") is not None:
            return "pyttsx3", "pyttsx3 offline synthesizer"

        return "unavailable", (
            f"No offline TTS engine active. Install piper-tts + place '{self.language.piper_voice}.onnx' "
            f"in {self.cfg.voices_dir} (or run on Windows 11 for built-in SAPI5)."
        )

    def _resolve_piper_binary(self) -> Optional[str]:
        if self.cfg.piper_binary and Path(self.cfg.piper_binary).is_file():
            return self.cfg.piper_binary
        found = shutil.which("piper") or shutil.which("piper.exe")
        if found:
            return found
        if self.cfg.voices_dir:
            cand = Path(self.cfg.voices_dir).parent / "piper" / ("piper.exe" if os.name == "nt" else "piper")
            if cand.is_file():
                return str(cand)
        return None

    def _resolve_piper_model(self, lang: LanguageProfile) -> Optional[Path]:
        if lang.piper_model_path:
            p = Path(lang.piper_model_path).expanduser()
            if p.is_file():
                return p
        if self.cfg.voices_dir:
            vdir = Path(self.cfg.voices_dir).expanduser()
            exact = vdir / f"{lang.piper_voice}.onnx"
            if exact.is_file():
                return exact
            # Match any installed voice for this language prefix (e.g. en_*.onnx or ta_*.onnx)
            if vdir.is_dir():
                matches = sorted(vdir.glob(f"{lang.code}_*.onnx"))
                if matches:
                    return matches[0]
        return None

    # ---------------- Speak & Interrupt ----------------

    def speak(self, raw_text: str, language: Optional[str] = None, interrupt_current: bool = True) -> str:
        """Sanitize text (removing code/technical dumps) and queue offline synthesis.

        Returns the sanitized text prepared for speech (empty string if muted/nothing speakable).
        """
        lang_code = language or self.language.code
        clean = self.sanitizer.sanitize(raw_text, language=lang_code)
        if not clean or self.cfg.muted or self.cfg.backend == "disabled":
            return ""
        if interrupt_current:
            self.stop()
        self._interrupt.clear()
        self._queue.put((clean, lang_code))
        return clean

    def stop(self) -> None:
        """Immediately halt ongoing audio playback and drain queued sentences."""
        self._interrupt.set()
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

        with self._proc_lock:
            proc = self._active_proc
            self._active_proc = None
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=0.8)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        # Stop sounddevice or winsound playback instantly
        if importlib.util.find_spec("sounddevice") is not None:
            try:
                import sounddevice as sd  # type: ignore

                sd.stop()
            except Exception:
                pass
        if os.name == "nt":
            try:
                import winsound

                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass

        if self._speaking.is_set():
            self._speaking.clear()
            self._emit_state(False, "")

    def shutdown(self) -> None:
        self._shutdown.set()
        self.stop()
        self._queue.put(None)
        if self._worker.is_alive():
            self._worker.join(timeout=2.0)

    def _emit_state(self, speaking: bool, text: str) -> None:
        if self._on_state:
            try:
                self._on_state(speaking, text)
            except Exception:
                pass

    # ---------------- Worker Loop ----------------

    def _worker_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                item = self._queue.get(timeout=0.3)
            except queue.Empty:
                continue
            if item is None:
                break

            clean_text, _lang_code = item
            if self.cfg.muted or self._interrupt.is_set():
                continue

            backend, _ = self.detect_backend()
            if backend in {"disabled", "unavailable"}:
                continue

            self._speaking.set()
            self._emit_state(True, clean_text)
            completed_cleanly = False
            try:
                sentences = SpeechSanitizer.split_sentences(clean_text)
                for sentence in sentences:
                    if self._interrupt.is_set() or self.cfg.muted:
                        break
                    if backend == "piper_python":
                        self._speak_piper_python(sentence)
                    elif backend == "piper_cli":
                        self._speak_piper_cli(sentence)
                    elif backend == "sapi5":
                        self._speak_windows_sapi5(sentence)
                    elif backend == "pyttsx3":
                        self._speak_pyttsx3(sentence)
                completed_cleanly = not self._interrupt.is_set()
            except Exception as exc:
                log.warning("TTS synthesis error (%s): %s", backend, exc)
            finally:
                self._speaking.clear()
                self._emit_state(False, "")
                if completed_cleanly and self._on_finished:
                    try:
                        self._on_finished()
                    except Exception:
                        pass

    def _play_wav_bytes(self, wav_bytes: bytes) -> None:
        if self._interrupt.is_set() or not wav_bytes:
            return
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            sr = wf.getframerate()
            channels = wf.getnchannels()
            raw = wf.readframes(wf.getnframes())

        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if channels > 1:
            samples = samples.reshape(-1, channels)
        # Apply user volume cleanly
        samples = np.clip(samples * float(self.cfg.volume), -1.0, 1.0)

        played = False
        if importlib.util.find_spec("sounddevice") is not None:
            try:
                import sounddevice as sd  # type: ignore

                sd.play(samples, samplerate=sr, blocking=False)
                duration = len(samples) / float(sr)
                waited = 0.0
                while waited < duration and not self._interrupt.is_set():
                    self._interrupt.wait(timeout=0.05)
                    waited += 0.05
                if self._interrupt.is_set():
                    sd.stop()
                else:
                    sd.wait()
                played = True
            except Exception as exc:
                log.warning("sounddevice playback error (%s); falling back to winsound.", exc)

        if not played and os.name == "nt" and not self._interrupt.is_set():
            import winsound

            scaled = (samples * 32767.0).astype(np.int16)
            fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="jarvis_tts_")
            os.close(fd)
            try:
                with wave.open(tmp, "wb") as wf:
                    wf.setnchannels(channels)
                    wf.setsampwidth(2)
                    wf.setframerate(sr)
                    wf.writeframes(scaled.tobytes())
                winsound.PlaySound(tmp, winsound.SND_FILENAME | winsound.SND_ASYNC)
                duration = len(samples) / float(sr)
                self._interrupt.wait(timeout=duration)
                if self._interrupt.is_set():
                    winsound.PlaySound(None, winsound.SND_PURGE)
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def _speak_piper_python(self, sentence: str) -> None:
        from piper.voice import PiperVoice  # type: ignore

        # Word-count hard cap: keep first 50 words before synthesis
        words = sentence.split()
        if len(words) > 50:
            log.debug("TTS word-cap: truncating from %d words to 50.", len(words))
            sentence = " ".join(words[:50])

        model_path = self._resolve_piper_model(self.language)
        if not model_path:
            self._speak_windows_sapi5(sentence)
            return
        key = str(model_path)
        voice = self._piper_voice_cache.get(key)
        if voice is None:
            voice = PiperVoice.load(key, use_cuda=False)
            self._piper_voice_cache[key] = voice

        buf = io.BytesIO()
        try:
            length_scale = round(1.0 / max(0.5, float(self.cfg.speed)), 2)
            with wave.open(buf, "wb") as wf:
                if hasattr(voice, "synthesize_wav"):
                    try:
                        from piper.config import SynthesisConfig  # type: ignore

                        syn_cfg = SynthesisConfig(length_scale=length_scale, volume=self.cfg.volume)
                    except Exception:
                        syn_cfg = None
                    voice.synthesize_wav(sentence, wf, syn_config=syn_cfg)
                else:
                    voice.synthesize(sentence, wf, length_scale=length_scale)
            audio = buf.getvalue()
            if audio:
                self._play_wav_bytes(audio)
            else:
                self._speak_windows_sapi5(sentence)
        except Exception as exc:
            log.warning("Piper neural TTS error (%s), falling back to Windows SAPI5: %s", sentence[:30], exc)
            self._speak_windows_sapi5(sentence)

    def _speak_piper_cli(self, sentence: str) -> None:
        binary = self._resolve_piper_binary()
        model_path = self._resolve_piper_model(self.language)
        if not binary or not model_path:
            return
        fd, tmp_wav = tempfile.mkstemp(suffix=".wav", prefix="jarvis_piper_")
        os.close(fd)
        try:
            length_scale = f"{1.0 / max(0.5, self.cfg.speed):.3f}"
            cmd = [
                binary,
                "--model", str(model_path),
                "--length_scale", length_scale,
                "--output_file", tmp_wav,
            ]
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
            )
            with self._proc_lock:
                self._active_proc = proc
            proc.communicate(input=sentence.encode("utf-8"), timeout=20.0)
            with self._proc_lock:
                self._active_proc = None
            if proc.returncode == 0 and os.path.isfile(tmp_wav):
                self._play_wav_bytes(Path(tmp_wav).read_bytes())
        finally:
            if os.path.exists(tmp_wav):
                try:
                    os.remove(tmp_wav)
                except OSError:
                    pass

    def _speak_windows_sapi5(self, sentence: str) -> None:
        """Interruptible Windows 11 SAPI5 speech via isolated PowerShell process."""
        # Map speed [0.5, 2.0] -> SAPI Rate [-5, +6]
        sapi_rate = max(-8, min(8, int(round((self.cfg.speed - 1.0) * 6.0))))
        sapi_vol = max(0, min(100, int(round(self.cfg.volume * 100))))
        hint = (self.language.sapi_voice_hint or "").replace("'", "")
        ps_script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.Rate = {sapi_rate}; $s.Volume = {sapi_vol}; "
            f"$hint = '{hint}'; "
            "if ($hint) { "
            "  $v = $s.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Name -match $hint } | Select-Object -First 1; "
            "  if ($v) { $s.SelectVoice($v.VoiceInfo.Name) } "
            "} "
            "$text = [Console]::In.ReadToEnd(); "
            "if ($text) { $s.Speak($text) }"
        )
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        with self._proc_lock:
            self._active_proc = proc
        try:
            proc.communicate(input=sentence.encode("utf-8"), timeout=45.0)
        except subprocess.TimeoutExpired:
            proc.kill()
        finally:
            with self._proc_lock:
                self._active_proc = None

    def _speak_pyttsx3(self, sentence: str) -> None:
        import pyttsx3  # type: ignore

        engine = pyttsx3.init()
        engine.setProperty("rate", int(175 * self.cfg.speed))
        engine.setProperty("volume", float(self.cfg.volume))
        engine.say(sentence)
        engine.runAndWait()
