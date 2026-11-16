"""Threaded Microphone Listener with Wake-Word, Continuous, Push-to-Talk & Barge-In."""

from __future__ import annotations

import importlib.util
import queue
import threading
import time
from typing import Callable, Optional, Tuple

import numpy as np

from jarvis.config.schema import LanguageProfile, STTConfig
from jarvis.logs.logger import get_logger
from jarvis.voice.stt_engine import SpeechToTextEngine
from jarvis.voice.vad import AdaptiveVAD
from jarvis.voice.wake_word import WakeWordDetector

log = get_logger("voice.listener")


class VoiceListener:
    """Manages microphone capture, VAD, wake-word activation, PTT & TTS barge-in."""

    def __init__(
        self,
        stt_cfg: STTConfig,
        stt_engine: SpeechToTextEngine,
        language_profile: LanguageProfile,
        *,
        on_transcript: Callable[[str, str], None],
        on_state_change: Optional[Callable[[str, str], None]] = None,
        on_audio_level: Optional[Callable[[float], None]] = None,
        on_barge_in: Optional[Callable[[], None]] = None,
        is_tts_speaking: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.cfg = stt_cfg
        self.stt = stt_engine
        self.language = language_profile
        self._on_transcript = on_transcript
        self._on_state = on_state_change
        self._on_level = on_audio_level
        self._on_barge_in = on_barge_in
        self._is_tts_speaking = is_tts_speaking

        self.vad = AdaptiveVAD(
            sample_rate=stt_cfg.sample_rate,
            energy_threshold_db=stt_cfg.vad_energy_threshold_db,
            silence_duration_sec=stt_cfg.vad_silence_duration_sec,
            min_speech_sec=stt_cfg.vad_min_speech_sec,
            max_utterance_sec=stt_cfg.max_utterance_sec,
        )
        self.wake = WakeWordDetector(
            wake_phrases=language_profile.wake_phrases,
            use_openwakeword=stt_cfg.use_openwakeword,
            oww_model_name=stt_cfg.openwakeword_model,
            oww_threshold=stt_cfg.openwakeword_threshold,
        )

        self._audio_queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=120)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._ptt_active = threading.Event()
        self._acoustic_armed_until: float = 0.0
        self._followup_until: float = 0.0
        self._barge_in_frames: int = 0
        self._last_level_emit: float = 0.0

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def mode(self) -> str:
        return self.cfg.listen_mode

    def set_mode(self, mode: str) -> None:
        if mode not in {"wake_word", "continuous", "push_to_talk"}:
            raise ValueError(f"Unsupported listen_mode: {mode}")
        self.cfg.listen_mode = mode  # type: ignore[assignment]
        self.vad.reset()
        if self.is_running:
            self._emit_state("listening", f"Mode: {mode}")

    def set_language(self, profile: LanguageProfile) -> None:
        self.language = profile
        self.wake.update_phrases(profile.wake_phrases)

    def open_followup_window(self) -> None:
        """Allow user to respond immediately after Jarvis speaks without re-saying 'Jarvis'."""
        if self.cfg.followup_window_sec > 0:
            self._followup_until = time.time() + self.cfg.followup_window_sec

    def check_microphone(self) -> Tuple[bool, str]:
        if importlib.util.find_spec("sounddevice") is None:
            return False, "sounddevice is not installed. Run: pip install sounddevice"
        stt_ok, stt_msg = self.stt.check_availability()
        if not stt_ok:
            return False, stt_msg
        try:
            import sounddevice as sd  # type: ignore

            dev = sd.query_devices(kind="input")
            return True, f"Microphone ready: {dev.get('name', 'Default Input')}"
        except Exception as exc:
            return False, f"No working microphone detected: {exc}"

    def start(self) -> Tuple[bool, str]:
        """Begin background audio capture & VAD loop."""
        if self.is_running:
            return True, "Voice listener already active."
        ok, msg = self.check_microphone()
        if not ok:
            self._emit_state("unavailable", msg)
            return False, msg

        self._stop_event.clear()
        self.vad.reset()
        self._running = True
        self._thread = threading.Thread(
            target=self._capture_loop,
            name="jarvis-voice-listener",
            daemon=True,
        )
        self._thread.start()
        self._emit_state("listening", f"Listening ({self.cfg.listen_mode})")
        return True, f"Started listening ({self.cfg.listen_mode}). {msg}"

    def stop(self) -> None:
        """Stop microphone capture cleanly."""
        self._stop_event.set()
        self._ptt_active.clear()
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self.vad.reset()
        self._emit_level(0.0)
        self._emit_state("idle", "Microphone stopped")

    def ptt_press(self) -> Tuple[bool, str]:
        """Begin Push-to-Talk recording."""
        if not self.is_running:
            prev = self.cfg.listen_mode
            self.cfg.listen_mode = "push_to_talk"
            ok, msg = self.start()
            if not ok:
                self.cfg.listen_mode = prev  # type: ignore[assignment]
                return False, msg
        if self._on_barge_in:
            self._on_barge_in()
        self.vad.reset()
        self._ptt_active.set()
        self._emit_state("recording_ptt", "Push-to-Talk active — speak now")
        return True, "Recording..."

    def ptt_release(self) -> None:
        """Finalize Push-to-Talk utterance and send for transcription."""
        was_active = self._ptt_active.is_set()
        self._ptt_active.clear()
        if not was_active:
            return
        audio = self.vad.flush()
        if audio is not None:
            threading.Thread(
                target=self._handle_utterance,
                args=(audio, True),
                daemon=True,
            ).start()
        elif self.is_running:
            self._emit_state("listening", f"Listening ({self.cfg.listen_mode})")

    def _emit_state(self, state: str, detail: str = "") -> None:
        if self._on_state:
            try:
                self._on_state(state, detail)
            except Exception:
                pass

    def _emit_level(self, level: float) -> None:
        now = time.monotonic()
        if level > 0.0 and (now - self._last_level_emit) < 0.04:
            return
        self._last_level_emit = now
        if self._on_level:
            try:
                self._on_level(level)
            except Exception:
                pass

    def _capture_loop(self) -> None:
        import sounddevice as sd  # type: ignore

        blocksize = int(self.cfg.sample_rate * 0.08)  # 80ms frame = 1280 samples @ 16kHz (matches openWakeWord!)

        def audio_callback(indata, frames, time_info, status) -> None:  # noqa: ANN001
            if self._stop_event.is_set():
                return
            try:
                self._audio_queue.put_nowait(indata[:, 0].copy())
            except queue.Full:
                pass

        try:
            with sd.InputStream(
                samplerate=self.cfg.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=blocksize,
                callback=audio_callback,
            ):
                while not self._stop_event.is_set():
                    try:
                        frame = self._audio_queue.get(timeout=0.2)
                    except queue.Empty:
                        self.stt.unload_if_idle()
                        continue
                    self._process_frame(frame)
        except Exception as exc:
            log.error("Audio capture loop terminated: %s", exc)
            self._running = False
            self._emit_state("error", f"Microphone error: {exc}")

    def _process_frame(self, frame: np.ndarray) -> None:
        speaking_now = bool(self._is_tts_speaking and self._is_tts_speaking())

        # Barge-in handling while TTS is speaking
        if speaking_now:
            if not self.cfg.barge_in_enabled:
                return
            clean = AdaptiveVAD.preprocess_audio(frame)
            db = AdaptiveVAD.compute_rms_db(clean)
            # Require stronger energy (+9 dB over threshold) for 3 consecutive frames (~240ms) to interrupt TTS
            if db >= (self.cfg.vad_energy_threshold_db + 9.0):
                self._barge_in_frames += 1
                if self._barge_in_frames >= 3 and self._on_barge_in:
                    log.info("Voice barge-in detected (%.1f dB) — interrupting speech output.", db)
                    self._barge_in_frames = 0
                    self._on_barge_in()
                    self._followup_until = time.time() + self.cfg.followup_window_sec
            else:
                self._barge_in_frames = 0
            return
        self._barge_in_frames = 0

        # Push-to-talk mode ignores audio when button isn't held
        if self.cfg.listen_mode == "push_to_talk" and not self._ptt_active.is_set():
            self._emit_level(0.0)
            return

        # Acoustic wake-word detection when idle in wake_word mode
        now = time.time()
        in_followup = now < self._followup_until
        if (
            self.cfg.listen_mode == "wake_word"
            and self.wake.has_acoustic_model
            and not self._ptt_active.is_set()
            and not in_followup
            and now >= self._acoustic_armed_until
        ):
            clean = AdaptiveVAD.preprocess_audio(frame)
            self._emit_level(max(0.0, min(1.0, (AdaptiveVAD.compute_rms_db(clean) + 58.0) / 48.0)) * 0.4)
            if self.wake.check_audio_frame(clean):
                self._acoustic_armed_until = now + 8.0
                self.vad.reset()
                self._emit_state("wake_triggered", "Wake word heard — listening for command")
            return

        res = self.vad.process_frame(frame)
        self._emit_level(res.level_normalized)

        if res.utterance_complete and res.utterance_audio is not None:
            bypass_wake = (
                self._ptt_active.is_set()
                or self.cfg.listen_mode in {"continuous", "push_to_talk"}
                or in_followup
                or (now < self._acoustic_armed_until)
            )
            self._acoustic_armed_until = 0.0
            self._handle_utterance(res.utterance_audio, bypass_wake)

    def _handle_utterance(self, audio: np.ndarray, bypass_wake: bool) -> None:
        self._emit_state("transcribing", "Transcribing speech locally...")
        result = self.stt.transcribe(audio, language=self.language.whisper_code)
        if self.is_running:
            self._emit_state("listening", f"Listening ({self.cfg.listen_mode})")

        if result.error:
            log.warning("STT warning: %s", result.error)
            return
        text = result.text.strip()
        if not text:
            return

        if bypass_wake:
            # Still strip leading "Jarvis," cleanly if user spoke it anyway
            match = self.wake.match_transcript(text)
            final_cmd = match.remainder_command if (match.detected and match.remainder_command) else text
            if final_cmd:
                self._on_transcript(final_cmd, result.language)
            return

        # Wake-word mode via transcript check
        match = self.wake.match_transcript(text)
        if not match.detected:
            return
        if match.remainder_command:
            self._on_transcript(match.remainder_command, result.language)
        else:
            # User called "Jarvis" alone — respond conversationally and open follow-up window!
            self._followup_until = time.time() + max(8.0, self.cfg.followup_window_sec)
            self._emit_state("wake_triggered", "Yes? Listening for your command...")
            greet = "வணக்கம் ஜார்விஸ்" if result.language == "ta" else "Hello Jarvis"
            self._on_transcript(greet, result.language)
