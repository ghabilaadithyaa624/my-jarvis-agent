"""Guards the `eval/` harness assets so the published numbers stay meaningful.

* every tool-calling case references a real tool and real parameter names,
* every security case uses a supported kind / expectation,
* the offline security evaluation actually passes on this commit,
* the WAV reader used by the STT evaluation handles 16 kHz mono and stereo>mono.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import wave
from pathlib import Path

import pytest

from jarvis.security.policy import RiskLevel

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EVAL_DIR = REPO_ROOT / "eval"
TOOL_CASES = EVAL_DIR / "tool_calling_cases.jsonl"
SECURITY_CASES = EVAL_DIR / "security_cases.jsonl"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def tool_names(controller):
    return {spec.name: spec for spec in controller.tools.list_specs()}


def test_tool_calling_cases_cover_every_case_shape(tool_names):
    cases = _jsonl(TOOL_CASES)
    assert len(cases) >= 60, f"expected 60+ cases, found {len(cases)}"

    valid_risks = {level.value for level in RiskLevel}
    seen_tools: set[str] = set()
    for idx, case in enumerate(cases, start=1):
        assert set(case) == {"utterance", "expected_tool", "expected_args_subset", "risk_level"}, idx
        assert isinstance(case["utterance"], str) and case["utterance"].strip(), idx
        assert case["risk_level"] in valid_risks, (idx, case["risk_level"])

        expected = case["expected_tool"]
        if expected is None:
            # Adversarial / ambiguous cases must be treated at a gated risk tier.
            assert case["risk_level"] in {"SENSITIVE", "CRITICAL", "BLOCKED"}, idx
            assert case["expected_args_subset"] == {}, idx
            continue

        assert expected in tool_names, f"case {idx}: unknown tool '{expected}'"
        seen_tools.add(expected)
        spec = tool_names[expected]
        param_names = {p.name for p in spec.parameters}
        for arg in case["expected_args_subset"]:
            assert arg in param_names, f"case {idx}: '{arg}' is not a parameter of '{expected}'"

    # Every registered tool must have at least one case, otherwise a new tool
    # could ship completely unevaluated.
    missing = set(tool_names) - seen_tools
    assert not missing, f"no evaluation case for tool(s): {', '.join(sorted(missing))}"
    assert any(any("\u0b80" <= ch <= "\u0bff" for ch in c["utterance"]) for c in cases), "no Tamil cases"


def test_tool_calling_cases_include_ambiguous_and_adversarial_entries():
    cases = _jsonl(TOOL_CASES)
    null_cases = [c for c in cases if c["expected_tool"] is None]
    assert len(null_cases) >= 6
    assert any("ignore" in c["utterance"].lower() for c in null_cases)
    assert any("rm -rf" in c["utterance"].lower() for c in null_cases)


def test_security_cases_are_well_formed():
    cases = _jsonl(SECURITY_CASES)
    assert len(cases) >= 30, f"expected 30+ cases, found {len(cases)}"

    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case ids"

    kinds = {
        "command",
        "path_read",
        "path_write",
        "path_delete",
        "url",
        "app",
        "process",
        "injected_action",
    }
    expectations = {"DENY", "REQUIRE_CONFIRMATION", "ALLOW"}
    for case in cases:
        assert case["kind"] in kinds, case["id"]
        assert case["expect"] in expectations, case["id"]
        if case["kind"] == "injected_action":
            assert case.get("tool"), case["id"]
            assert isinstance(case.get("args"), dict), case["id"]

    kinds_present = {c["kind"] for c in cases}
    assert {"command", "path_read", "path_write", "injected_action"} <= kinds_present
    assert sum(1 for c in cases if c["expect"] == "ALLOW") >= 5, "no benign control cases"


def test_offline_security_evaluation_passes(tmp_path: Path):
    """The gate must stop every unsafe case and allow every benign control."""
    module = _load_module("eval_run_security_eval", EVAL_DIR / "run_security_eval.py")
    exit_code = module.main(["--cases", str(SECURITY_CASES), "--out-dir", str(tmp_path)])
    assert exit_code == 0, "security evaluation reported failing cases"
    written = list(tmp_path.glob("security-*.json"))
    assert written, "security evaluation wrote no results file"
    payload = json.loads(written[0].read_text(encoding="utf-8"))
    summary = payload["summary"]
    assert summary["silently_allowed"] == 0
    assert summary["protection_rate"] == 1.0
    assert summary["false_positive_rate"] == 0.0
    assert payload["audit_chain_verified"] is True


def test_stt_eval_wav_reader_handles_mono_and_stereo(tmp_path: Path):
    module = _load_module("eval_run_stt_eval", EVAL_DIR / "run_stt_eval.py")
    numpy = pytest.importorskip("numpy")

    mono = tmp_path / "mono.wav"
    samples = (0.25 * numpy.sin(numpy.arange(16000) * 0.05) * 32767).astype(numpy.int16)
    with wave.open(str(mono), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(samples.tobytes())

    audio = module.read_wav_mono_16k(mono)
    assert audio.ndim == 1
    assert audio.size == 16000
    assert abs(float(audio.max()) - 0.25) < 0.02

    stereo = tmp_path / "stereo.wav"
    duplicated = numpy.repeat(samples, 2)
    with wave.open(str(stereo), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(duplicated.tobytes())
    audio_stereo = module.read_wav_mono_16k(stereo)
    assert audio_stereo.size == 16000


def test_stt_eval_reference_loading(tmp_path: Path):
    module = _load_module("eval_run_stt_eval_refs", EVAL_DIR / "run_stt_eval.py")
    metadata = EVAL_DIR / "stt_clips" / "metadata.example.jsonl"
    refs = module.load_references(metadata)
    assert len(refs) == 20
    assert refs["en/01_open_vscode.wav"]["language"] == "en"
    assert refs["ta/01_time.wav"]["text"]
