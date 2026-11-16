#!/usr/bin/env python
"""Security / prompt-injection evaluation — runs entirely offline (no Ollama).

Every case is pushed through the **real** ``SecurityGate`` (which owns
``PathGuard``, ``CommandValidator``, ``AppAllowlistValidator`` and
``WebSafetyValidator``) and then through ``SecurityGate.authorize()`` with *no*
confirmation channel registered — the fail-closed configuration.

Case kinds
----------
``command``          raw shell command handed to ``run_terminal_command``
``path_read``        path handed to ``read_document``
``path_write``       path handed to ``create_file`` (``overwrite=True``)
``path_delete``      path handed to ``delete_path``
``url``              URL handed to ``open_website``
``app``             app name handed to ``open_application``
``process``          process name handed to ``stop_process``
``injected_action``  a prompt-injection string plus the tool call a *fully
                     compromised* model would emit; the gate must still stop it

Reported metrics
----------------
* ``block_rate``       : share of unsafe cases hard-DENIED
* ``confirm_rate``     : share of unsafe cases escalated to human confirmation
* ``protection_rate``  : share of unsafe cases that were never silently allowed
* ``false_positive_rate`` : share of benign control cases that were wrongly gated

Usage
-----
    python eval/run_security_eval.py
    python eval/run_security_eval.py --cases eval/security_cases.jsonl --verbose
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # allow `python eval/run_security_eval.py`
    sys.path.insert(0, str(REPO_ROOT))

from jarvis.config.schema import SecurityConfig  # noqa: E402
from jarvis.logs.logger import AuditLogger  # noqa: E402
from jarvis.security.emergency import EmergencyStopController  # noqa: E402
from jarvis.security.gate import PermissionDeniedError, SecurityGate  # noqa: E402
from jarvis.security.policy import RiskLevel  # noqa: E402

DEFAULT_CASES = Path(__file__).resolve().parent / "security_cases.jsonl"
DEFAULT_RESULTS_DIR = Path(__file__).resolve().parent / "results"

#: tool + default risk used to drive ``SecurityGate._evaluate_tool`` per case kind.
KIND_TO_TOOL: Dict[str, tuple[str, RiskLevel]] = {
    "command": ("run_terminal_command", RiskLevel.MODERATE),
    "path_read": ("read_document", RiskLevel.SAFE),
    "path_write": ("create_file", RiskLevel.MODERATE),
    "path_delete": ("delete_path", RiskLevel.SENSITIVE),
    "url": ("open_website", RiskLevel.MODERATE),
    "app": ("open_application", RiskLevel.MODERATE),
    "process": ("stop_process", RiskLevel.SENSITIVE),
}

UNSAFE_EXPECTATIONS = {"DENY", "REQUIRE_CONFIRMATION"}


@dataclass
class SecurityCase:
    id: str
    kind: str
    value: str
    expect: str
    note: str = ""
    tool: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    setup: List[str] = field(default_factory=list)

    def tool_call(self, workdir: Path) -> tuple[str, RiskLevel, Dict[str, Any]]:
        """Resolve the (tool, risk, args) triple this case exercises."""
        if self.kind == "injected_action":
            tool = self.tool
            risk = {
                "delete_path": RiskLevel.SENSITIVE,
                "create_file": RiskLevel.MODERATE,
                "read_document": RiskLevel.SAFE,
                "run_terminal_command": RiskLevel.MODERATE,
                "open_website": RiskLevel.MODERATE,
                "stop_process": RiskLevel.SENSITIVE,
            }.get(tool, RiskLevel.MODERATE)
            resolved = {k: _substitute(v, workdir) for k, v in self.args.items()}
            return tool, risk, resolved

        tool, risk = KIND_TO_TOOL[self.kind]
        value = _substitute(self.value, workdir)
        if self.kind == "command":
            return tool, risk, {"command": value, "cwd": str(workdir / "workspace")}
        if self.kind == "path_read":
            return tool, risk, {"path": value}
        if self.kind == "path_write":
            return tool, risk, {"path": value, "content": "written by eval", "overwrite": True}
        if self.kind == "path_delete":
            return tool, risk, {"path": value}
        if self.kind == "url":
            return tool, risk, {"url": value}
        if self.kind == "app":
            return tool, risk, {"app_name": value}
        if self.kind == "process":
            return tool, risk, {"name_or_pid": value}
        raise SystemExit(f"Unknown case kind: {self.kind}")


def _substitute(value: Any, workdir: Path) -> Any:
    if isinstance(value, str):
        return (
            value.replace("{workdir}", str(workdir))
            .replace("{outside}", str(workdir.parent / "outside_permitted"))
        )
    if isinstance(value, dict):
        return {k: _substitute(v, workdir) for k, v in value.items()}
    return value


def build_gate(root: Path) -> SecurityGate:
    """A realistic gate: permitted roots, no confirmation channel (fail-closed)."""
    workdir = root / "jarvis_sandbox"
    for sub in ("Documents", "Downloads", "Desktop", "workspace", "../outside_permitted"):
        (workdir / sub).resolve().mkdir(parents=True, exist_ok=True)
    cfg = SecurityConfig(
        permitted_directories=[
            str(workdir / "Documents"),
            str(workdir / "Downloads"),
            str(workdir / "Desktop"),
            str(workdir / "workspace"),
        ],
    )
    audit = AuditLogger(root / "audit.jsonl")
    return SecurityGate(
        security_cfg=cfg,
        audit_logger=audit,
        emergency=EmergencyStopController(),
        confirmation_handler=None,
        workspace_dir=workdir / "workspace",
    )


def load_cases(path: Path) -> List[SecurityCase]:
    cases: List[SecurityCase] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            cases.append(
                SecurityCase(
                    id=str(raw["id"]),
                    kind=str(raw["kind"]),
                    value=str(raw.get("value", "")),
                    expect=str(raw["expect"]),
                    note=str(raw.get("note", "")),
                    tool=str(raw.get("tool", "")),
                    args=dict(raw.get("args") or {}),
                    setup=list(raw.get("setup") or []),
                )
            )
            assert line_no  # keep flake-style linters quiet
    return cases


def run_case(gate: SecurityGate, case: SecurityCase, workdir: Path) -> Dict[str, Any]:
    tool, risk, args = case.tool_call(workdir)

    check = gate._evaluate_tool(tool, risk, args)
    decision = check.decision.value

    # Enforce through the public fail-closed entry point.
    denied = False
    denial_reason = ""
    try:
        gate.authorize(tool, risk, args)
    except PermissionDeniedError as exc:
        denied = True
        denial_reason = str(exc)

    if decision == "DENY":
        enforced = "DENY"
    elif decision == "REQUIRE_CONFIRMATION":
        # No handler registered => hard denial (fail-closed), never silent execution.
        enforced = "CONFIRM" if denied else "ALLOWED_WITHOUT_CONFIRMATION"
    else:
        enforced = "ALLOW"

    # Normalise the expectation vocabulary to the enforcement vocabulary.
    expected_enforced = "CONFIRM" if case.expect == "REQUIRE_CONFIRMATION" else case.expect
    passed = enforced == expected_enforced

    return {
        "id": case.id,
        "kind": case.kind,
        "tool": tool,
        "value": case.value[:160],
        "expected": case.expect,
        "decision": decision,
        "enforced": enforced,
        "stopped": denied,
        "passed": passed,
        "reason": check.reason[:240],
        "denial": denial_reason[:240],
        "note": case.note,
    }


def summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    unsafe = [r for r in results if r["expected"] in UNSAFE_EXPECTATIONS]
    benign = [r for r in results if r["expected"] == "ALLOW"]

    hard_blocked = [r for r in unsafe if r["enforced"] == "DENY"]
    confirmed = [r for r in unsafe if r["enforced"] == "CONFIRM"]
    silently_allowed = [r for r in unsafe if r["enforced"] == "ALLOW"]
    wrongly_gated = [r for r in benign if r["enforced"] != "ALLOW"]

    def rate(part: int, whole: int) -> float:
        return round(part / whole, 4) if whole else 0.0

    return {
        "total_cases": len(results),
        "unsafe_cases": len(unsafe),
        "benign_control_cases": len(benign),
        "hard_blocked": len(hard_blocked),
        "confirmed": len(confirmed),
        "silently_allowed": len(silently_allowed),
        "block_rate": rate(len(hard_blocked), len(unsafe)),
        "confirm_rate": rate(len(confirmed), len(unsafe)),
        "protection_rate": rate(len(hard_blocked) + len(confirmed), len(unsafe)),
        "false_positive_rate": rate(len(wrongly_gated), len(benign)),
        "failures": [r["id"] for r in results if not r["passed"]],
        "false_positives": [r["id"] for r in wrongly_gated],
        "silently_allowed_ids": [r["id"] for r in silently_allowed],
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Prompt-injection & unsafe-command evaluation (offline).")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--out-dir", default=str(DEFAULT_RESULTS_DIR))
    parser.add_argument("--verbose", action="store_true", help="Print every case")
    args = parser.parse_args(argv)

    cases = load_cases(Path(args.cases))
    if not cases:
        print("No cases found.")
        return 1

    with tempfile.TemporaryDirectory(prefix="jarvis_security_eval_") as tmp:
        root = Path(tmp)
        gate = build_gate(root)
        workdir = root / "jarvis_sandbox"
        for case in cases:
            for rel in case.setup:
                target = Path(_substitute(rel, workdir))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("existing content", encoding="utf-8")

        results = [run_case(gate, case, workdir) for case in cases]

        chain_ok, chain_len, _msg = gate.audit.verify_chain()

    summary = summarize(results)

    if args.verbose:
        print(f"{'id':<16}{'expected':<24}{'decision':<22}{'enforced':<28}pass")
        for r in results:
            print(
                f"{r['id']:<16}{r['expected']:<24}{r['decision']:<22}{r['enforced']:<28}"
                f"{'OK' if r['passed'] else 'FAIL'}  {r['note']}"
            )

    print("\n=== SecurityGate evaluation ===")
    print(f"  cases              : {summary['total_cases']} "
          f"({summary['unsafe_cases']} unsafe, {summary['benign_control_cases']} benign controls)")
    print(f"  hard blocked       : {summary['hard_blocked']}/{summary['unsafe_cases']} "
          f"({summary['block_rate']:.1%})")
    print(f"  escalated to human : {summary['confirmed']}/{summary['unsafe_cases']} "
          f"({summary['confirm_rate']:.1%})")
    print(f"  silently allowed   : {summary['silently_allowed']} "
          f"({1 - summary['protection_rate']:.1%} of unsafe cases)")
    print(f"  false positives    : {summary['false_positive_rate']:.1%} of benign controls")
    print(f"  audit chain        : {'verified' if chain_ok else 'BROKEN'} ({chain_len} entries)")
    if summary["failures"]:
        print(f"  FAILED CASES      : {', '.join(summary['failures'])}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "cases_file": Path(args.cases).name,
        "summary": summary,
        "audit_chain_verified": chain_ok,
        "results": results,
    }
    out_path = out_dir / f"security-{stamp}.json"
    if out_path.exists():
        out_path = out_dir / f"security-{stamp}-{datetime.now().strftime('%H%M%S')}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {out_path}")

    return 0 if not summary["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
