#!/usr/bin/env python
"""Speech-to-text (WER) evaluation on your own recorded clips.

Runs the **same** ``SpeechToTextEngine`` JARVIS uses at runtime (faster-whisper,
CPU int8 by default) over a folder of 16 kHz mono WAV clips and compares the
transcripts with your reference text using ``jiwer`` (word + character error
rate).

Clip pairing — two options
--------------------------
1. Sidecar files::

       eval/stt_clips/en/01_open_vscode.wav
       eval/stt_clips/en/01_open_vscode.txt   # reference transcript

2. A metadata file (``--references``), JSONL or CSV::

       {"file": "01_open_vscode.wav", "text": "Jarvis open VS Code", "language": "en"}
       file,text,language
       01_open_vscode.wav,Jarvis open VS Code,en

Requires two optional packages::

    pip install -e ".[voice,eval]"     # faster-whisper + jiwer

Usage
-----
    python eval/run_stt_eval.py --audio-dir eval/stt_clips --lang en
    python eval/run_stt_eval.py --audio-dir eval/stt_clips --lang ta --model small
    python eval/run_stt_eval.py --audio-dir eval/stt_clips --references eval/stt_clips/metadata.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import wave
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # allow `python eval/run_stt_eval.py`
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_RESULTS_DIR = Path(__file__).resolve().parent / "results"


def _build_transform(jiwer):
    """Case/punctuation-insensitive comparison transform (version-tolerant)."""
    return jiwer.Compose(
        [
            jiwer.ToLowerCase(),
            jiwer.RemovePunctuation(),
            jiwer.RemoveMultipleSpaces(),
            jiwer.Strip(),
            jiwer.ReduceToListOfListOfWords(),
        ]
    )


def _error_rate(jiwer, metric: str, reference: str, hypothesis: str, transform) -> float:
    """Call jiwer.wer/cer with whichever transform keyword this version supports."""
    fn = getattr(jiwer, metric)
    try:
        return float(fn(reference, hypothesis, reference_transform=transform, hypothesis_transform=transform))
    except TypeError:  # jiwer < 4 used truth=/hypothesis=
        return float(fn(reference, hypothesis, truth=transform, hypothesis=transform))


def _ref_key(name: str) -> str:
    """Normalise a metadata ``file`` entry to a POSIX relative path."""
    return Path(str(name).strip().replace("\\", "/")).as_posix().lstrip("./")


def load_references(path: Path) -> Dict[str, Dict[str, Any]]:
    """Load a JSONL or CSV metadata file keyed by normalised relative file path."""
    refs: Dict[str, Dict[str, Any]] = {}
    if not path.is_file():
        raise SystemExit(f"Reference file not found: {path}")
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                name = (row.get("file") or "").strip()
                if name:
                    refs[_ref_key(name)] = {
                        "text": (row.get("text") or "").strip(),
                        "language": (row.get("language") or "").strip(),
                    }
        return refs
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        raw = json.loads(line)
        name = _ref_key(str(raw.get("file", "")))
        if name:
            refs[name] = {
                "text": str(raw.get("text", "")).strip(),
                "language": str(raw.get("language", "")).strip(),
            }
    return refs


def collect_clips(audio_dir: Path, references: Optional[Path]) -> List[Dict[str, Any]]:
    refs = load_references(references) if references else {}
    clips: List[Dict[str, Any]] = []
    for wav_path in sorted(audio_dir.rglob("*.wav")):
        reference = ""
        language = ""
        meta = None
        if refs:
            rel = wav_path.relative_to(audio_dir).as_posix()
            for candidate in (rel, wav_path.name, f"{wav_path.parent.name}/{wav_path.name}"):
                if candidate in refs:
                    meta = refs[candidate]
                    break
        if meta:
            reference, language = meta["text"], meta["language"]
        else:
            sidecar = wav_path.with_suffix(".txt")
            if sidecar.is_file():
                reference = sidecar.read_text(encoding="utf-8").strip()
        if not reference:
            print(f"! skipping {wav_path.name}: no reference transcript found")
            continue
        clips.append({"file": wav_path, "reference": reference, "language": language})
    return clips


def read_wav_mono_16k(path: Path):
    """Read a PCM WAV file into float32 mono @16 kHz (no external deps)."""
    import numpy as np

    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())

    if width != 2:
        raise SystemExit(f"{path.name}: expected 16-bit PCM WAV, got {width * 8}-bit")
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels == 2:
        audio = audio.reshape(-1, 2).mean(axis=1)
    if rate != 16000:
        # Simple linear resampling keeps this dependency-free; record at 16 kHz.
        duration = audio.size / rate
        target = int(duration * 16000)
        audio = np.interp(
            np.linspace(0.0, audio.size - 1, target, dtype=np.float32),
            np.arange(audio.size, dtype=np.float32),
            audio,
        ).astype(np.float32)
        print(f"  note: {path.name} was {rate} Hz — resampled to 16 kHz (recording at 16 kHz is more accurate)")
    return audio


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="WER evaluation for the local speech-to-text engine.")
    parser.add_argument("--audio-dir", required=True, help="Folder containing 16 kHz mono WAV clips")
    parser.add_argument("--references", default=None, help="Optional metadata .jsonl/.csv with file,text[,language]")
    parser.add_argument("--lang", default="en", choices=["en", "ta", "auto"], help="Language hint for Whisper")
    parser.add_argument("--model", default="small", help="faster-whisper model size (tiny/base/small/medium)")
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"], help="cpu keeps VRAM free for Ollama")
    parser.add_argument("--compute-type", default="int8", help="CTranslate2 compute type")
    parser.add_argument("--out-dir", default=str(DEFAULT_RESULTS_DIR))
    args = parser.parse_args(argv)

    audio_dir = Path(args.audio_dir)
    if not audio_dir.is_dir():
        print(f"Audio directory not found: {audio_dir}", file=sys.stderr)
        return 2

    try:
        import jiwer
    except ImportError:
        print(
            "jiwer is not installed — it is only needed for evaluation.\n"
            '    pip install -e ".[eval]"\n'
            "(file: pyproject.toml -> project.optional-dependencies -> eval)",
            file=sys.stderr,
        )
        return 2

    try:
        from faster_whisper import WhisperModel  # noqa: F401
    except ImportError:
        print(
            "faster-whisper is not installed — it provides the local STT engine.\n"
            '    pip install -e ".[voice]"',
            file=sys.stderr,
        )
        return 2

    clips = collect_clips(audio_dir, Path(args.references) if args.references else None)
    if not clips:
        print(
            f"No usable clips found in {audio_dir}.\n"
            "Record 10 English and 10 Tamil clips as described in eval/README.md "
            "(16 kHz mono WAV + .txt sidecar transcript).",
            file=sys.stderr,
        )
        return 1

    from jarvis.config.schema import STTConfig
    from jarvis.voice.stt_engine import SpeechToTextEngine

    stt_cfg = STTConfig(backend="faster_whisper", model_size=args.model, device=args.device, compute_type=args.compute_type)
    engine = SpeechToTextEngine(stt_cfg)
    ok, msg = engine.check_availability()
    if not ok:
        print(f"STT engine unavailable: {msg}", file=sys.stderr)
        return 2
    print(msg)

    rows: List[Dict[str, Any]] = []
    for idx, clip in enumerate(clips, start=1):
        audio = read_wav_mono_16k(clip["file"])
        language = clip["language"] or (None if args.lang == "auto" else args.lang)
        result = engine.transcribe(audio, language=language)
        hypothesis = (result.text or "").strip()
        reference = clip["reference"]
        transform = _build_transform(jiwer)
        if hypothesis:
            wer = _error_rate(jiwer, "wer", reference, hypothesis, transform)
            cer = _error_rate(jiwer, "cer", reference, hypothesis, transform)
        else:
            wer = cer = 1.0
        rows.append(
            {
                "file": clip["file"].name,
                "language": language or "auto",
                "reference": reference,
                "hypothesis": hypothesis,
                "wer": round(float(wer), 4),
                "cer": round(float(cer), 4),
                "duration_ms": round(result.duration_ms, 1),
            }
        )
        print(f"  [{idx:>2}/{len(clips)}] {clip['file'].name:<32} WER={wer:6.2%}  CER={cer:6.2%}  {hypothesis[:70]}")

    mean_wer = sum(r["wer"] for r in rows) / len(rows)
    mean_cer = sum(r["cer"] for r in rows) / len(rows)
    corpus_wer = _error_rate(
        jiwer,
        "wer",
        [r["reference"] for r in rows],
        [r["hypothesis"] for r in rows],
        _build_transform(jiwer),
    )

    summary = {
        "clips": len(rows),
        "model": args.model,
        "device": args.device,
        "compute_type": args.compute_type,
        "language_hint": args.lang,
        "mean_wer": round(mean_wer, 4),
        "mean_cer": round(mean_cer, 4),
        "corpus_wer": round(corpus_wer, 4),
    }
    print("\n=== STT evaluation ===")
    print(f"  clips            : {summary['clips']}")
    print(f"  model / device   : {args.model} / {args.device} ({args.compute_type})")
    print(f"  mean WER         : {mean_wer:.2%}")
    print(f"  corpus WER       : {corpus_wer:.2%}")
    print(f"  mean CER         : {mean_cer:.2%}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"stt-{date.today().isoformat()}.json"
    if out_path.exists():
        out_path = out_dir / f"stt-{date.today().isoformat()}-{datetime.now().strftime('%H%M%S')}.json"
    out_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "audio_dir": str(audio_dir),
                "summary": summary,
                "clips": rows,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
