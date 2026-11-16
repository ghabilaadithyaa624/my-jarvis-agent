"""Regression tests for the `jarvis.logs` package and for repo import integrity.

These tests exist because `.gitignore` used to contain a bare `logs/` rule,
which silently excluded the *source package* `jarvis/logs/` from git. A fresh
clone therefore crashed on `import jarvis` with a `ModuleNotFoundError`, caught
only by the import-integrity test below.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import jarvis
from jarvis.logs.logger import (
    REDACTION_PLACEHOLDER,
    AuditLogger,
    get_logger,
    redact_text,
    setup_logging,
)

REPO_ROOT = Path(jarvis.__file__).resolve().parent.parent
PACKAGE_ROOT = Path(jarvis.__file__).resolve().parent


# ---------------------------------------------------------------------------
# 1. The package imports and logging works
# ---------------------------------------------------------------------------


def test_logs_package_imports_and_exports():
    import jarvis.logs as logs_pkg

    assert hasattr(logs_pkg, "AuditLogger")
    assert hasattr(logs_pkg, "get_logger")
    assert hasattr(logs_pkg, "setup_logging")

    logger = get_logger("tests.logs")
    assert logger.name == "jarvis.tests.logs"


def test_setup_logging_writes_redacted_rotating_log(tmp_path: Path):
    log_dir = tmp_path / "logs"
    setup_logging(log_dir, console=False)
    log = get_logger("tests.redaction")
    log.info("ollama api_key=sk-supersecretvalue0123456789 password=hunter2 ready")

    written = (log_dir / "jarvis.log").read_text(encoding="utf-8")
    assert "sk-supersecretvalue0123456789" not in written
    assert "hunter2" not in written
    assert REDACTION_PLACEHOLDER in written


def test_redact_text_masks_common_credential_shapes():
    samples = [
        ("token=ghp_0123456789abcdefghijklmnopqrstuvwx", "ghp_0123456789"),
        ("Authorization: Bearer abcdef1234567890xyz", "abcdef1234567890xyz"),
        ('{"api_key": "AKIAIOSFODNN7EXAMPLE"}', "AKIAIOSFODNN7EXAMPLE"),
    ]
    for raw, secret in samples:
        assert secret not in redact_text(raw)


# ---------------------------------------------------------------------------
# 2. Audit chain: verifies, and detects tampering
# ---------------------------------------------------------------------------


def test_audit_chain_verifies_and_chains_hashes(tmp_path: Path):
    audit_path = tmp_path / "audit.jsonl"
    audit = AuditLogger(audit_path)
    first = audit.record("PERMISSION_CHECK", "open_vscode", "ALLOWED", "MODERATE")
    second = audit.record("TOOL_OUTCOME", "open_vscode", "SUCCESS", "MODERATE", verified=True)

    assert first["prev_hash"] == "0" * 64
    assert second["prev_hash"] == first["entry_hash"]
    assert len(first["entry_hash"]) == 64

    ok, count, message = audit.verify_chain()
    assert ok is True
    assert count == 2
    assert "verified" in message.lower()

    # A fresh logger instance over the same file recovers the chain head
    resumed = AuditLogger(audit_path)
    third = resumed.record("CONFIRMATION", "delete_path", "USER_REJECTED", "SENSITIVE")
    assert third["seq"] == 3
    assert third["prev_hash"] == second["entry_hash"]
    assert resumed.verify_chain()[0] is True


def test_audit_tampering_with_one_line_breaks_verify_chain(tmp_path: Path):
    audit_path = tmp_path / "audit.jsonl"
    audit = AuditLogger(audit_path)
    for idx in range(3):
        audit.record("PERMISSION_CHECK", f"action_{idx}", "ALLOWED", "SAFE")

    lines = audit_path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["decision"] = "DENIED"
    lines[1] = json.dumps(tampered)
    audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, count, message = audit.verify_chain()
    assert ok is False
    assert count == 1
    assert "line 2" in message.lower()


def test_audit_deleting_a_line_breaks_verify_chain(tmp_path: Path):
    audit_path = tmp_path / "audit.jsonl"
    audit = AuditLogger(audit_path)
    for idx in range(3):
        audit.record("PERMISSION_CHECK", f"action_{idx}", "ALLOWED", "SAFE")

    lines = audit_path.read_text(encoding="utf-8").splitlines()
    audit_path.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")

    ok, _, message = audit.verify_chain()
    assert ok is False
    assert "line 2" in message.lower()


def test_audit_redacts_secrets_on_disk(tmp_path: Path):
    audit_path = tmp_path / "audit.jsonl"
    audit = AuditLogger(audit_path)
    audit.record(
        "CONFIRMATION",
        "run_terminal_command",
        "USER_REJECTED",
        "SENSITIVE",
        {"password": "s3cr3t-value", "api_key": "sk-livesecret1234567890"},
    )
    raw = audit_path.read_text(encoding="utf-8")
    assert "s3cr3t-value" not in raw
    assert "sk-livesecret1234567890" not in raw
    assert REDACTION_PLACEHOLDER in raw

    recent = audit.read_recent(limit=5)
    assert recent[0]["action"] == "run_terminal_command"


def test_empty_chain_is_valid(tmp_path: Path):
    assert AuditLogger(tmp_path / "missing.jsonl").verify_chain()[0] is True


# ---------------------------------------------------------------------------
# 3. Import integrity: every `jarvis.*` import must resolve to a real file
# ---------------------------------------------------------------------------


def _module_path_exists(dotted: str) -> bool:
    """Return True when `dotted` resolves to a .py module or package dir."""
    parts = dotted.split(".")
    if parts[0] != "jarvis":
        return False
    base = REPO_ROOT.joinpath(*parts)
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def _collect_jarvis_imports(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "jarvis" or alias.name.startswith("jarvis."):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative imports resolve inside the package
                continue
            if node.module and (node.module == "jarvis" or node.module.startswith("jarvis.")):
                found.add(node.module)
    return found


def test_every_jarvis_import_resolves_to_an_existing_file():
    missing: list[str] = []
    scanned = 0
    for py_file in sorted(PACKAGE_ROOT.rglob("*.py")):
        scanned += 1
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for dotted in _collect_jarvis_imports(tree):
            if not _module_path_exists(dotted):
                missing.append(f"{dotted} (imported by {py_file.relative_to(REPO_ROOT)})")
    assert scanned > 10, "Import scan found suspiciously few Python files"
    assert not missing, "Imports reference modules that do not exist:\n" + "\n".join(sorted(missing))


def test_source_packages_exist_in_repository():
    for package in ("logs", "brain", "security", "tools", "voice", "speech"):
        assert (PACKAGE_ROOT / package / "__init__.py").is_file(), f"jarvis/{package} is missing"


def test_gitignore_does_not_ignore_source_packages():
    """A bare `logs/` rule regressed the packaged `jarvis/logs/` module before."""
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    rules = [line.strip() for line in gitignore.splitlines()]
    offending = [r for r in rules if r in {"logs/", "logs", "/logs", "/logs/"} and not r.startswith("/")]
    assert not offending, f".gitignore must use a root-anchored '/logs/' rule, found {offending}"
    assert "/logs/" in rules


@pytest.mark.skipif(not (REPO_ROOT / ".git").exists(), reason="not a git checkout")
def test_logs_package_is_not_gitignored():
    """git check-ignore must not match the packaged logger module."""
    import subprocess

    result = subprocess.run(
        ["git", "check-ignore", "-v", "jarvis/logs/logger.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, f"jarvis/logs/logger.py is still ignored: {result.stdout}"
