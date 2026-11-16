"""Phase 3 Tests: AdaptiveVAD, English & Tamil Wake-Word, SpeechSanitizer & TTS."""

from __future__ import annotations

import numpy as np

from jarvis.config.schema import LanguageProfile, TTSConfig
from jarvis.speech.sanitizer import SpeechSanitizer
from jarvis.speech.tts_engine import SpeechEngine
from jarvis.voice.vad import AdaptiveVAD
from jarvis.voice.wake_word import WakeWordDetector


def test_adaptive_vad_segments_speech_and_ignores_silence():
    sr = 16000
    frame_len = 1280  # 80ms
    vad = AdaptiveVAD(
        sample_rate=sr,
        energy_threshold_db=-34.0,
        silence_duration_sec=0.4,
        min_speech_sec=0.24,
    )

    # 1. Low ambient noise (-55 dB) should never trigger speech
    rng = np.random.default_rng(42)
    for _ in range(8):
        quiet = rng.normal(0.0, 0.001, frame_len).astype(np.float32)
        res = vad.process_frame(quiet)
        assert res.is_speech is False
        assert res.utterance_complete is False

    # 2. Simulated 480ms voiced speech (220Hz tone at -14 dB)
    t = np.arange(frame_len * 6) / sr
    voice = (0.2 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
    for idx in range(6):
        chunk = voice[idx * frame_len : (idx + 1) * frame_len]
        res = vad.process_frame(chunk)
        assert res.is_speech is True
        assert res.level_normalized > 0.5

    # 3. Trailing silence completes utterance and yields clean PCM
    completed_audio = None
    for _ in range(8):
        quiet = np.zeros(frame_len, dtype=np.float32)
        res = vad.process_frame(quiet)
        if res.utterance_complete:
            completed_audio = res.utterance_audio
            break

    assert completed_audio is not None
    assert completed_audio.size >= int(0.45 * sr)


def test_wake_word_matching_english_and_tamil():
    ww = WakeWordDetector(
        wake_phrases=["hey jarvis", "okay jarvis", "jarvis", "ஜார்விஸ்", "ஹே ஜார்விஸ்"],
        use_openwakeword=False,
    )

    m1 = ww.match_transcript("Jarvis, open VS Code.")
    assert m1.detected is True
    assert m1.remainder_command == "open VS Code."

    m2 = ww.match_transcript("Hey Jarvis! Check my laptop performance")
    assert m2.detected is True
    assert m2.remainder_command == "Check my laptop performance"

    # Tamil wake word + Tamil command
    m3 = ww.match_transcript("ஜார்விஸ், இப்பொழுது நேரம் என்ன?")
    assert m3.detected is True
    assert m3.remainder_command == "இப்பொழுது நேரம் என்ன?"

    # Standalone wake word
    m4 = ww.match_transcript("Jarvis")
    assert m4.detected is True
    assert m4.remainder_command == ""

    # Unrelated background speech must not trigger
    m5 = ww.match_transcript("Please pass me the water bottle.")
    assert m5.detected is False


def test_speech_sanitizer_never_reads_code_blocks_or_technical_dumps():
    san = SpeechSanitizer(max_chars=500, announce_code_omission=True)

    raw = (
        "### Script Created\n"
        "I created `C:\\Users\\Arun\\Projects\\jarvis\\hello_world.py` and executed it.\n\n"
        "```python\n"
        "import os\n"
        "def compute_fibonacci(n):\n"
        "    return n if n < 2 else compute_fibonacci(n-1) + compute_fibonacci(n-2)\n"
        "print(compute_fibonacci(10))\n"
        "```\n\n"
        "The output was **55**. See https://docs.python.org/3/tutorial for more."
    )
    spoken = san.sanitize(raw, language="en")
    assert "compute_fibonacci" not in spoken
    assert "import os" not in spoken
    assert "https://" not in spoken
    assert "C:\\Users" not in spoken
    assert "hello_world.py" in spoken
    assert "55" in spoken
    assert "conversation panel" in spoken

    # Tamil language support
    ta_spoken = san.sanitize("இதோ உங்கள் நிரல்:\n```python\nprint(123)\n```", language="ta")
    assert "print" not in ta_spoken
    assert "இதோ உங்கள் நிரல்" in ta_spoken
    assert "உரையாடல் பலகையில்" in ta_spoken


def test_tts_engine_controls_mute_speed_volume_and_clean_shutdown(tmp_path):
    cfg = TTSConfig(backend="auto", voices_dir=str(tmp_path), speed=1.0, volume=0.8, muted=False)
    prof = LanguageProfile(
        code="en",
        display_name="English",
        whisper_code="en",
        wake_phrases=["jarvis"],
        piper_voice="en_GB-alan-medium",
    )
    engine = SpeechEngine(cfg, prof)
    try:
        engine.set_speed(1.25)
        assert engine.cfg.speed == 1.25
        engine.set_volume(0.6)
        assert engine.cfg.volume == 0.6

        engine.set_muted(True)
        assert engine.speak("Hello world") == ""

        engine.set_muted(False)
        clean = engine.speak("Result ready.\n```python\nx = 1\n```")
        assert "Result ready" in clean and "x = 1" not in clean
        engine.stop()
        assert engine.is_speaking is False
    finally:
        engine.shutdown()
