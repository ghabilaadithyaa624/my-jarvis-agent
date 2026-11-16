#!/usr/bin/env python
"""Tool-calling evaluation harness for local Ollama models.

For every test utterance this runs the *real* JARVIS planning stack:

    IntentClassifier  ->  TaskPlanner  ->  category-scoped tool catalog
                      ->  local Ollama model (streamed)
                      ->  StructuredOutputParser

**Nothing is executed.** No tool handler is ever invoked, no file is touched,
no process is started, no browser is opened — only the planning path runs.

Reported metrics per model:

* ``tool_selection_accuracy`` — did the model pick the expected tool?
* ``argument_accuracy``       — given the right tool, do the expected arguments match?
* ``json_validity_rate``      — did the model emit parseable structured JSON?
* ``p50_latency_ms`` / ``p95_latency_ms`` — full response time per utterance
* ``p50_ttft_ms``  / ``p95_ttft_ms``      — time to first streamed token

Results are written to ``eval/results/<YYYY-MM-DD>.json`` plus a Markdown table
(``eval/results/<YYYY-MM-DD>.md``) that can be pasted into the README.

Usage
-----
    python eval/run_tool_eval.py                                  # all installed models
    python eval/run_tool_eval.py --models qwen2.5:7b,qwen2.5:3b   # explicit list
    python eval/run_tool_eval.py --limit 20 --temperature 0.0     # quick smoke run
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # allow `python eval/run_tool_eval.py`
    sys.path.insert(0, str(REPO_ROOT))

from jarvis.app.controller import JarvisController  # noqa: E402
from jarvis.brain.output_parser import StructuredOutputParser  # noqa: E402
from jarvis.brain.prompts import build_agent_system_prompt  # noqa: E402
from jarvis.config.loader import load_config  # noqa: E402
from jarvis.models.ollama_client import OllamaError  # noqa: E402

DEFAULT_CASES = Path(__file__).resolve().parent / "tool_calling_cases.jsonl"
DEFAULT_RESULTS_DIR = Path(__file__).resolve().parent / "results"


# ---------------------------------------------------------------------------
# Case loading & matching helpers
# ---------------------------------------------------------------------------


@dataclass
class EvalCase:
    utterance: str
    expected_tool: Optional[str]
    expected_args_subset: Dict[str, Any]
    risk_level: str


@dataclass
class CaseResult:
    index: int
    utterance: str
    expected_tool: Optional[str]
    predicted_tool: str
    predicted_args: Dict[str, Any]
    json_valid: bool
    tool_match: bool
    args_match: bool
    latency_ms: float
    ttft_ms: float
    raw_output: str
    error: str = ""


@dataclass
class ModelReport:
    model: str
    cases: List[CaseResult] = field(default_factory=list)
    notes: str = ""

    # -- metric helpers -------------------------------------------------

    @property
    def total(self) -> int:
        return len(self.cases)

    def _rate(self, predicate) -> float:
        if not self.cases:
            return 0.0
        return sum(1 for c in self.cases if predicate(c)) / len(self.cases)

    @property
    def tool_selection_accuracy(self) -> float:
        return self._rate(lambda c: c.tool_match)

    @property
    def json_validity_rate(self) -> float:
        return self._rate(lambda c: c.json_valid)

    @property
    def argument_accuracy(self) -> float:
        """Argument accuracy measured on cases where the correct tool was chosen."""
        matched = [c for c in self.cases if c.tool_match and c.expected_tool is not None]
        if not matched:
            return 0.0
        return sum(1 for c in matched if c.args_match) / len(matched)

    @property
    def end_to_end_accuracy(self) -> float:
        return self._rate(lambda c: c.tool_match and c.args_match)

    def percentile(self, values: List[float], pct: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        if len(ordered) == 1:
            return ordered[0]
        rank = (len(ordered) - 1) * pct
        low = int(rank)
        high = min(low + 1, len(ordered) - 1)
        return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)

    @property
    def latency_stats(self) -> Dict[str, float]:
        lat = [c.latency_ms for c in self.cases if c.latency_ms > 0]
        return {
            "p50_latency_ms": round(self.percentile(lat, 0.50), 1),
            "p95_latency_ms": round(self.percentile(lat, 0.95), 1),
            "mean_latency_ms": round(statistics.fmean(lat), 1) if lat else 0.0,
        }

    @property
    def ttft_stats(self) -> Dict[str, float]:
        ttft = [c.ttft_ms for c in self.cases if c.ttft_ms > 0]
        return {
            "p50_ttft_ms": round(self.percentile(ttft, 0.50), 1),
            "p95_ttft_ms": round(self.percentile(ttft, 0.95), 1),
            "mean_ttft_ms": round(statistics.fmean(ttft), 1) if ttft else 0.0,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "notes": self.notes,
            "case_count": self.total,
            "tool_selection_accuracy": round(self.tool_selection_accuracy, 4),
            "argument_accuracy": round(self.argument_accuracy, 4),
            "end_to_end_accuracy": round(self.end_to_end_accuracy, 4),
            "json_validity_rate": round(self.json_validity_rate, 4),
            **self.latency_stats,
            **self.ttft_stats,
            "cases": [c.__dict__ for c in self.cases],
        }


def load_cases(path: Path, limit: Optional[int] = None) -> List[EvalCase]:
    cases: List[EvalCase] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_no}: invalid JSON ({exc})") from exc
            cases.append(
                EvalCase(
                    utterance=str(raw["utterance"]),
                    expected_tool=raw.get("expected_tool"),
                    expected_args_subset=dict(raw.get("expected_args_subset") or {}),
                    risk_level=str(raw.get("risk_level", "")),
                )
            )
    if limit:
        cases = cases[:limit]
    return cases


def _values_match(expected: Any, actual: Any) -> bool:
    """Loose comparison: numbers by value, strings by containment, booleans strictly."""
    if actual is None:
        return expected is None
    if isinstance(expected, bool):
        return isinstance(actual, bool) and actual is expected
    if isinstance(expected, (int, float)):
        try:
            return abs(float(actual) - float(expected)) < 1e-6
        except (TypeError, ValueError):
            return False
    expected_text = str(expected).strip().strip("\"'").lower().replace("\\", "/")
    actual_text = str(actual).strip().strip("\"'").lower().replace("\\", "/")
    if not expected_text:
        return True
    return expected_text == actual_text or expected_text in actual_text


def args_match(expected: Dict[str, Any], actual: Dict[str, Any]) -> bool:
    if not expected:
        return True
    if not isinstance(actual, dict):
        return False
    return all(_values_match(value, actual.get(key)) for key, value in expected.items())


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    except OSError:  # pragma: no cover - git missing
        return "unknown"


# ---------------------------------------------------------------------------
# Core evaluation loop
# ---------------------------------------------------------------------------


def resolve_models(ctrl: JarvisController, requested: Optional[str]) -> List[str]:
    """--models wins; otherwise use installed models that also appear in the config fallback list."""
    installed = [m.name for m in ctrl.models.refresh_models()]
    if requested:
        wanted = [m.strip() for m in requested.split(",") if m.strip()]
        missing = [m for m in wanted if installed and m not in installed]
        if missing:
            print(f"! not installed locally (will still be attempted): {', '.join(missing)}")
        return wanted

    fallback = list(ctrl.config.ollama.fallback_models)
    chosen = [name for name in fallback if name in installed]
    if not chosen:
        chosen = installed
    return chosen


def evaluate_model(ctrl: JarvisController, model: str, cases: List[EvalCase], args) -> ModelReport:
    report = ModelReport(model=model)
    parser = StructuredOutputParser()
    valid_tools = set(ctrl.tools.list_names())
    workspace = ctrl.data_dir / "workspace"
    language_hint = ctrl.config.active_language_profile().system_prompt_hint

    for idx, case in enumerate(cases, start=1):
        # --- real planning stack (no execution) -------------------------
        analysis = ctrl.agent.intent_classifier.analyze(case.utterance)
        plan = ctrl.agent.planner.create_initial_plan(case.utterance, analysis)
        categories = analysis.categories or None
        catalog = ctrl.tools.format_prompt_catalog(categories)
        system_prompt = build_agent_system_prompt(
            tool_catalog=catalog,
            workspace_dir=workspace,
            permitted_dirs=list(ctrl.config.security.permitted_directories),
            language_hint=language_hint,
            memories=[],
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": case.utterance},
        ]

        started = time.perf_counter()
        first_chunk_at = 0.0
        pieces: List[str] = []
        error = ""
        try:
            stream = ctrl.models.client.chat_stream(
                model,
                messages,
                temperature=args.temperature,
                num_ctx=args.num_ctx,
                max_tokens=args.max_tokens,
                json_mode=not args.no_json_mode,
            )
            while True:
                try:
                    piece = next(stream)
                except StopIteration:
                    break
                if piece and not first_chunk_at:
                    first_chunk_at = time.perf_counter() - started
                pieces.append(piece)
            elapsed = time.perf_counter() - started
        except OllamaError as exc:
            error = str(exc)
            elapsed = time.perf_counter() - started
        except Exception as exc:  # pragma: no cover - defensive
            error = f"{type(exc).__name__}: {exc}"
            elapsed = time.perf_counter() - started

        raw = "".join(pieces)
        step = parser.parse(raw, valid_tools=valid_tools) if raw else None
        predicted_tool = ""
        predicted_args: Dict[str, Any] = {}
        json_valid = False
        if step is not None and step.kind == "tool_call":
            predicted_tool = step.tool_name
            predicted_args = step.tool_args
            json_valid = True
        elif step is not None and step.kind == "final_answer":
            # A valid structured refusal / clarification answer.
            json_valid = True
            predicted_tool = "final_answer"

        if case.expected_tool is None:
            tool_ok = predicted_tool == "final_answer"
        else:
            tool_ok = predicted_tool == case.expected_tool

        report.cases.append(
            CaseResult(
                index=idx,
                utterance=case.utterance,
                expected_tool=case.expected_tool,
                predicted_tool=predicted_tool,
                predicted_args=predicted_args,
                json_valid=json_valid,
                tool_match=tool_ok,
                args_match=args_match(case.expected_args_subset, predicted_args),
                latency_ms=round(elapsed * 1000.0, 1),
                ttft_ms=round(first_chunk_at * 1000.0, 1),
                raw_output=raw[:600],
                error=error,
            )
        )

        status = "OK " if tool_ok else "MISS"
        print(
            f"  [{idx:>3}/{len(cases)}] {status} {case.utterance[:52]:<52} "
            f"expected={case.expected_tool or 'no-tool':<24} "
            f"got={predicted_tool or '-':<24} {elapsed * 1000.0:7.0f}ms"
            + (f"  err={error[:60]}" if error else ""),
            flush=True,
        )

        if plan.steps:
            report.notes = f"planner produced {len(plan.steps)} step(s) for first case"

    return report


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def score_fast_path(ctrl: JarvisController, cases: List[EvalCase]) -> Dict[str, Any]:
    """Model-independent baseline: how often does the deterministic fast path already win?"""
    hits = 0
    for case in cases:
        analysis = ctrl.agent.intent_classifier.analyze(case.utterance)
        predicted = analysis.fast_tool or "final_answer"
        if (case.expected_tool is None and predicted == "final_answer") or predicted == case.expected_tool:
            hits += 1
    return {
        "fast_path_accuracy": round(hits / len(cases), 4) if cases else 0.0,
        "description": "Deterministic IntentClassifier fast path (no LLM call) — baseline, not a model result.",
    }


def markdown_table(reports: List[ModelReport], fast_path: Dict[str, Any]) -> str:
    header = (
        "| Model | Cases | Tool selection | Arguments | End-to-end | JSON valid | "
        "p50 latency | p95 latency | p50 TTFT | p95 TTFT |\n"
        "|---|---|---|---|---|---|---|---|---|---|\n"
    )
    rows = []
    for r in reports:
        lat = r.latency_stats
        ttft = r.ttft_stats
        rows.append(
            f"| `{r.model}` | {r.total} | {r.tool_selection_accuracy:.1%} | "
            f"{r.argument_accuracy:.1%} | {r.end_to_end_accuracy:.1%} | {r.json_validity_rate:.1%} | "
            f"{lat['p50_latency_ms'] / 1000:.2f} s | {lat['p95_latency_ms'] / 1000:.2f} s | "
            f"{ttft['p50_ttft_ms'] / 1000:.2f} s | {ttft['p95_ttft_ms'] / 1000:.2f} s |"
        )
    note = (
        f"\n_Deterministic fast-path baseline on the same cases: "
        f"{fast_path['fast_path_accuracy']:.1%} (no model involved)._\n"
    )
    return header + "\n".join(rows) + "\n" + note


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Tool-calling accuracy & latency evaluation (planning only).")
    parser.add_argument("--models", default=None, help="Comma-separated Ollama models (default: installed)")
    parser.add_argument("--cases", default=str(DEFAULT_CASES), help="JSONL case file")
    parser.add_argument("--out-dir", default=str(DEFAULT_RESULTS_DIR), help="Where to write results")
    parser.add_argument("--data-dir", default=None, help="JARVIS data directory override")
    parser.add_argument("--limit", type=int, default=None, help="Only run the first N cases")
    parser.add_argument("--temperature", type=float, default=0.0, help="Sampling temperature (default 0.0)")
    parser.add_argument("--num-ctx", type=int, default=None, help="Override num_ctx")
    parser.add_argument("--max-tokens", type=int, default=None, help="Override max output tokens")
    parser.add_argument("--no-json-mode", action="store_true", help="Disable Ollama JSON mode")
    args = parser.parse_args(argv)

    cases = load_cases(Path(args.cases), args.limit)
    if not cases:
        print("No cases loaded — nothing to do.")
        return 1

    cfg = load_config(data_dir_override=args.data_dir)
    ctrl = JarvisController(cfg, start_background_services=False)
    try:
        models = resolve_models(ctrl, args.models)
        if not models:
            print(
                "No local Ollama models found.\n"
                "Start Ollama (`ollama serve`) and pull a model, e.g.:\n"
                "    ollama pull qwen2.5:7b\n"
                "Then re-run: python eval/run_tool_eval.py --models qwen2.5:7b"
            )
            return 1

        online = ctrl.models.client.check_health()
        if not online.get("online"):
            print(
                f"Local Ollama is not reachable at {cfg.ollama.base_url}.\n"
                "Start it with `ollama serve` and re-run this script. "
                "No results were written (numbers are never fabricated)."
            )
            return 1

        print(f"Evaluating {len(cases)} cases on {len(models)} model(s): {', '.join(models)}")
        reports: List[ModelReport] = []
        for model in models:
            print(f"\n=== {model} ===")
            try:
                reports.append(evaluate_model(ctrl, model, cases, args))
            except KeyboardInterrupt:
                print("\nInterrupted by user.")
                break

        fast_path = score_fast_path(ctrl, cases)
    finally:
        ctrl.shutdown()

    if not reports:
        print("No model finished — no results written.")
        return 1

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "git_commit": git_commit(),
        "cases_file": str(Path(args.cases).name),
        "case_count": len(cases),
        "ollama_base_url": cfg.ollama.base_url,
        "temperature": args.temperature,
        "num_ctx": args.num_ctx or cfg.ollama.num_ctx,
        "json_mode": not args.no_json_mode,
        "fast_path_baseline": fast_path,
        "models": [r.to_dict() for r in reports],
    }
    json_path = out_dir / f"{stamp}.json"
    # Never overwrite an earlier run silently: keep full timestamped history.
    if json_path.exists():
        json_path = out_dir / f"{stamp}-{datetime.now().strftime('%H%M%S')}.json"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    table = markdown_table(reports, fast_path)
    md_path = json_path.with_suffix(".md")
    md_path.write_text(
        f"# Tool-calling evaluation — {stamp}\n\n"
        f"- Models: {', '.join(r.model for r in reports)}\n"
        f"- Cases: {len(cases)} (`{Path(args.cases).name}`)\n"
        f"- Commit: `{payload['git_commit']}`\n"
        f"- Temperature: {args.temperature} · num_ctx: {payload['num_ctx']} · JSON mode: {payload['json_mode']}\n\n"
        + table,
        encoding="utf-8",
    )

    print("\n" + table)
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
