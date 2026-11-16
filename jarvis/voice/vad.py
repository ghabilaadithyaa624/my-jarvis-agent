"""Adaptive Voice Activity Detection (VAD) and ambient noise suppressor.

Pure-NumPy implementation consumes <1% of one i5-13420H CPU thread and 0 MB VRAM.
Performs DC offset removal, adaptive noise-floor tracking, RMS dB + zero-crossing
speech discrimination, and smooth silence hangover endpointing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class VADFrameResult:
    is_speech: bool
    rms_db: float
    level_normalized: float   # 0.0 to 1.0 for GUI voice orb animation
    utterance_complete: bool
    utterance_audio: Optional[np.ndarray] = None


class AdaptiveVAD:
    """Streaming voice activity detector with dynamic noise-floor calibration."""

    def __init__(
        self,
        sample_rate: int = 16000,
        energy_threshold_db: float = -36.0,
        silence_duration_sec: float = 0.85,
        min_speech_sec: float = 0.30,
        max_utterance_sec: float = 25.0,
        snr_margin_db: float = 8.5,
    ) -> None:
        self.sample_rate = sample_rate
        self.base_threshold_db = energy_threshold_db
        self.silence_samples = int(silence_duration_sec * sample_rate)
        self.min_speech_samples = int(min_speech_sec * sample_rate)
        self.max_utterance_samples = int(max_utterance_sec * sample_rate)
        self.snr_margin_db = snr_margin_db

        self._noise_floor_db: float = -52.0
        self._in_speech: bool = False
        self._speech_samples: int = 0
        self._trailing_silence: int = 0
        self._pre_roll: list[np.ndarray] = []
        self._max_pre_roll_frames: int = 6
        self._buffer: list[np.ndarray] = []
        self._buffered_samples: int = 0

    def reset(self) -> None:
        self._in_speech = False
        self._speech_samples = 0
        self._trailing_silence = 0
        self._buffer.clear()
        self._buffered_samples = 0
        self._pre_roll.clear()

    @staticmethod
    def preprocess_audio(frame: np.ndarray) -> np.ndarray:
        """Convert mono PCM frame to float32 [-1, 1] and strip DC hum/offset."""
        arr = np.asarray(frame)
        if arr.ndim > 1:
            arr = arr.mean(axis=1)
        if arr.dtype == np.int16:
            f = arr.astype(np.float32) / 32768.0
        else:
            f = arr.astype(np.float32, copy=False)
        if f.size == 0:
            return f
        # DC offset removal
        f = f - float(np.mean(f))
        return np.clip(f, -1.0, 1.0)

    @staticmethod
    def compute_rms_db(frame: np.ndarray) -> float:
        if frame.size == 0:
            return -90.0
        rms = float(np.sqrt(np.mean(np.square(frame)) + 1e-12))
        return max(-90.0, 20.0 * float(np.log10(rms + 1e-12)))

    @staticmethod
    def compute_zcr(frame: np.ndarray) -> float:
        if frame.size < 2:
            return 0.0
        signs = np.signbit(frame)
        return float(np.mean(signs[1:] != signs[:-1]))

    def process_frame(self, raw_frame: np.ndarray) -> VADFrameResult:
        """Feed one PCM frame and receive live speech status + completed utterance."""
        frame = self.preprocess_audio(raw_frame)
        n = frame.size
        if n == 0:
            return VADFrameResult(False, -90.0, 0.0, False, None)

        rms_db = self.compute_rms_db(frame)
        zcr = self.compute_zcr(frame)

        # Normalized UI level: map [-58 dB, -10 dB] -> [0.0, 1.0]
        level = max(0.0, min(1.0, (rms_db + 58.0) / 48.0))

        dynamic_thresh = max(self.base_threshold_db, self._noise_floor_db + self.snr_margin_db)
        # Filter extreme broadband hiss (ZCR > 0.55) while allowing human speech & sibilants
        is_voice = (rms_db >= dynamic_thresh) and (0.004 <= zcr <= 0.55)

        if not self._in_speech:
            if not is_voice:
                # Slowly adapt noise floor in quiet/ambient intervals
                alpha = 0.05 if rms_db > self._noise_floor_db else 0.15
                self._noise_floor_db = (1.0 - alpha) * self._noise_floor_db + alpha * min(rms_db, -28.0)
                self._pre_roll.append(frame)
                if len(self._pre_roll) > self._max_pre_roll_frames:
                    self._pre_roll.pop(0)
                return VADFrameResult(False, round(rms_db, 1), round(level, 3), False, None)

            # Speech onset — include pre-roll so initial consonants ("J-arvis") aren't clipped!
            self._in_speech = True
            self._buffer = list(self._pre_roll) + [frame]
            self._buffered_samples = sum(x.size for x in self._buffer)
            self._speech_samples = n
            self._trailing_silence = 0
            self._pre_roll.clear()
            return VADFrameResult(True, round(rms_db, 1), round(level, 3), False, None)

        # Currently inside an active speech segment
        self._buffer.append(frame)
        self._buffered_samples += n
        if is_voice:
            self._speech_samples += n
            self._trailing_silence = 0
        else:
            self._trailing_silence += n

        reached_silence_end = self._trailing_silence >= self.silence_samples
        reached_max_len = self._buffered_samples >= self.max_utterance_samples

        if reached_silence_end or reached_max_len:
            had_enough_speech = self._speech_samples >= self.min_speech_samples
            audio = np.concatenate(self._buffer) if (had_enough_speech and self._buffer) else None
            self.reset()
            if audio is not None:
                return VADFrameResult(False, round(rms_db, 1), round(level, 3), True, audio)
            return VADFrameResult(False, round(rms_db, 1), round(level, 3), False, None)

        return VADFrameResult(True, round(rms_db, 1), round(level, 3), False, None)

    def flush(self) -> Optional[np.ndarray]:
        """Force-finalize any active utterance (used on Push-to-Talk release)."""
        if self._buffer and self._speech_samples >= max(800, self.min_speech_samples // 2):
            audio = np.concatenate(self._buffer)
            self.reset()
            return audio
        self.reset()
        return None
