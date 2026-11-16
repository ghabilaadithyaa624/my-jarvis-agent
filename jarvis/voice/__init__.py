"""Voice input engine: VAD, Wake-Word detection, local Whisper STT & listener."""

from jarvis.voice.listener import VoiceListener
from jarvis.voice.stt_engine import SpeechToTextEngine, TranscriptionResult
from jarvis.voice.vad import AdaptiveVAD, VADFrameResult
from jarvis.voice.wake_word import WakeMatch, WakeWordDetector

__all__ = [
    "AdaptiveVAD",
    "SpeechToTextEngine",
    "TranscriptionResult",
    "VADFrameResult",
    "VoiceListener",
    "WakeMatch",
    "WakeWordDetector",
]
