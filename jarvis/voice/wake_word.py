"""Wake-word detector supporting openWakeWord (acoustic) + English & Tamil transcript matching."""

from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from jarvis.logs.logger import get_logger

log = get_logger("voice.wake_word")


@dataclass
class WakeMatch:
    detected: bool
    matched_phrase: str = ""
    remainder_command: str = ""


class WakeWordDetector:
    """Detects 'Jarvis' / 'Hey Jarvis' / 'ஜார்விஸ்' acoustically or in transcribed speech."""

    def __init__(
        self,
        wake_phrases: Optional[List[str]] = None,
        use_openwakeword: bool = True,
        oww_model_name: str = "hey_jarvis",
        oww_threshold: float = 0.55,
    ) -> None:
        self.wake_phrases = sorted(
            wake_phrases or ["hey jarvis", "ok jarvis", "okay jarvis", "jarvis", "ஜார்விஸ்"],
            key=len,
            reverse=True,
        )
        self.oww_model_name = oww_model_name
        self.oww_threshold = oww_threshold
        self._oww = None
        if use_openwakeword:
            self._try_load_openwakeword()

    def _try_load_openwakeword(self) -> None:
        if importlib.util.find_spec("openwakeword") is None:
            return
        try:
            from openwakeword.model import Model  # type: ignore

            self._oww = Model(wakeword_models=[self.oww_model_name], inference_framework="onnx")
            log.info("Loaded openWakeWord acoustic model '%s' on CPU.", self.oww_model_name)
        except Exception as exc:
            log.info("openWakeWord acoustic detector unavailable (using VAD+transcript mode): %s", exc)
            self._oww = None

    @property
    def has_acoustic_model(self) -> bool:
        return self._oww is not None

    def update_phrases(self, phrases: List[str]) -> None:
        if phrases:
            self.wake_phrases = sorted(phrases, key=len, reverse=True)

    def check_audio_frame(self, pcm_float32: np.ndarray) -> bool:
        """Check a 16kHz float32 audio chunk against openWakeWord ONNX model."""
        if self._oww is None or pcm_float32.size == 0:
            return False
        try:
            pcm_int16 = np.clip(pcm_float32 * 32767.0, -32768, 32767).astype(np.int16)
            scores = self._oww.predict(pcm_int16)
            for name, score in scores.items():
                if float(score) >= self.oww_threshold:
                    log.info("Acoustic wake word triggered: %s (score=%.2f)", name, float(score))
                    try:
                        self._oww.reset()
                    except Exception:
                        pass
                    return True
        except Exception:
            pass
        return False

    def match_transcript(self, text: str) -> WakeMatch:
        """Detect wake phrase in English or Tamil transcript and extract trailing command.

        Example:
            "Jarvis, open VS Code." -> WakeMatch(True, "jarvis", "open VS Code.")
            "ஜார்விஸ் நேரம் என்ன?"  -> WakeMatch(True, "ஜார்விஸ்", "நேரம் என்ன?")
        """
        cleaned = (text or "").strip()
        if not cleaned:
            return WakeMatch(False)

        # Strip leading filler punctuation/greetings
        stripped = re.sub(r"^[\s,.\-!?:;]+", "", cleaned)

        for phrase in self.wake_phrases:
            p = phrase.strip()
            if not p:
                continue
            # Match at beginning of utterance (allowing polite 'hi/hello' prefix)
            pattern = re.compile(
                rf"^(?:(?:hi|hello|yo|வணக்கம்)\s*[,!]?\s*)?({re.escape(p)})(?=$|[\s,.\-!?:;])(.*)$",
                flags=re.IGNORECASE | re.UNICODE,
            )
            m = pattern.match(stripped)
            if m:
                remainder = re.sub(r"^[\s,.\-!?:;]+", "", m.group(2)).strip()
                return WakeMatch(
                    detected=True,
                    matched_phrase=m.group(1),
                    remainder_command=remainder,
                )
        return WakeMatch(False)
