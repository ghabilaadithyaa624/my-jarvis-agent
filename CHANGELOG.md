# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

_Nothing yet._

## [1.0.0] - 2026-11-16

First public release. 100% local voice operator and desktop agent for Windows 11,
tuned for an Intel i5-13420H + NVIDIA RTX 3050 (6 GB VRAM) + 16 GB RAM laptop.

### Added

- **Voice pipeline** — AdaptiveVAD, offline wake words for English and Tamil
  (*"Hey Jarvis"* / *"ஜார்விஸ்"*), faster-whisper speech-to-text on CPU `int8`
  (0 MB VRAM), Piper neural TTS with a Windows SAPI5 fallback, speech sanitizer
  that never reads code blocks, stack traces or URLs aloud, and voice barge-in.
- **Agent brain** — hybrid `IntentClassifier` with a deterministic fast path,
  `TaskPlanner`, a model-independent `StructuredOutputParser` (JSON, fenced JSON,
  trailing commas, `<think>` traces, `<tool_call>` XML), an anti-hallucination
  guard and a 9-stage execute/verify loop with bounded steps and timeouts.
- **59 verified tools** across application control, filesystem, system telemetry,
  browser, developer/Blender workflows, notes, to-dos, reminders and memory — each
  with schema validation, risk level and post-action verification of real OS state.
- **Security** — single fail-closed `SecurityGate`; `PathGuard` (directory jail,
  `..`/symlink escape, protected system and credential paths), `CommandValidator`
  (destructive/obfuscated/chained shell blocked, installs and admin escalation
  require confirmation), application allowlist, sensitive-web guard, protected
  process immunity and an emergency stop.
- **Tamper-evident audit trail** — append-only JSONL where each entry stores the
  SHA-256 hash of the previous one, with `verify_chain()` and secret redaction.
- **Memory** — local SQLite (WAL + FTS5) conversation history, facts, notes,
  to-dos, reminders and timers.
- **Interfaces** — PySide6 dark HUD (orb visualiser, plan/stage view, permissions
  and audit panel, confirmation dialogs) and a full interactive CLI
  (`--cli`, `--once`, `--doctor`, `--self-test`).
- **Packaging** — non-destructive Windows setup wizard, launcher scripts, silent
  VBS launcher, least-privilege logon task registration and a PyInstaller onedir build.
- **Evaluation harness** (`eval/`) — tool-calling accuracy/argument accuracy/
  JSON validity/latency/TTFT per local model, an offline prompt-injection and
  unsafe-command suite through the `SecurityGate`, and a WER/CER harness with a
  recording protocol for 10 English + 10 Tamil clips.
- **CI** — lint plus tests on Windows and Linux across Python 3.10 and 3.12, and a
  core-only install smoke job; tag-triggered Windows release workflow.

### Changed

- `requirements.txt` is core-only (`httpx`, `pydantic`, `psutil`, `PyYAML`,
  `numpy`); GUI, voice, documents, Windows and browser packages moved to clearly
  documented `pyproject.toml` extras (`.[full]` installs everything).
- Missing optional packages now produce an actionable message
  (`pip install -e ".[gui]"`) with exit code 2 instead of a traceback.

### Fixed

- **`jarvis/logs/` was missing from the repository** because `.gitignore` contained
  a bare `logs/` rule that also matched the source package. Every fresh clone
  crashed on 14 `jarvis.logs.logger` imports. The rule is now anchored to `/logs/`,
  the package is restored, and an AST-based test fails CI if any `jarvis.*` import
  lacks its module file.
- `create_file(overwrite=True)` emitted by the model could overwrite an existing
  user file without a confirmation prompt; the gate now always requires human
  approval while `security.require_confirmation_for_overwrite` is enabled.
- The structured output parser reported a generic "not valid JSON" error when a
  model named an unknown tool; the specific rejection (e.g. `Unknown tool 'x'`)
  is now surfaced.

[Unreleased]: https://github.com/ghabilaadithyaa624/my-jarvis-agent/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/ghabilaadithyaa624/my-jarvis-agent/releases/tag/v1.0.0
