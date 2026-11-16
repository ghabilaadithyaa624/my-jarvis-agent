# Contributing to JARVIS PERSONAL AI

Short version: keep the core small, keep every new capability behind the
`SecurityGate`, and add a test that would have caught the bug.

## 1. Setup

```powershell
git clone https://github.com/ghabilaadithyaa624/my-jarvis-agent.git
cd my-jarvis-agent

python -m venv .venv
.\.venv\Scripts\Activate.ps1          # Linux/macOS: source .venv/bin/activate

# Core + test/lint tooling is enough for development on any OS
pip install -e ".[dev]"
```

Only install extras when you are working on that subsystem:

```powershell
pip install -e ".[gui]"        # PySide6 HUD
pip install -e ".[voice]"      # faster-whisper, Piper, wake word, mic
pip install -e ".[windows]"    # WASAPI volume, screenshots, Recycle Bin
pip install -e ".[documents]"  # .docx / .pdf / .xlsx readers
pip install -e ".[browser]"    # Playwright
pip install -e ".[eval]"       # jiwer (WER evaluation)
pip install -e ".[full]"       # everything
```

Ollama is **not** required for the test suite — the tests use a local mock HTTP
server (`jarvis/tests/conftest.py`).

## 2. Run the tests

```powershell
python -m pytest -q jarvis/tests        # full suite (~119 tests, no GPU/mic/Ollama)
python -m pytest -q jarvis/tests/test_security_hardening.py -k confirmation
python -m ruff check .                  # lint (E4/E7/E9/F)
```

Optional, but very welcome:

```powershell
python eval/run_security_eval.py        # offline, no model needed
python eval/run_tool_eval.py --limit 20 # needs local Ollama
```

CI runs lint plus the test suite on **Windows and Linux × Python 3.10 and 3.12**.
On Linux, tests that need Windows-only APIs must be skipped with
`@pytest.mark.skipif(os.name != "nt", ...)` rather than failing.

## 3. Code style

- **Python 3.10+**, `from __future__ import annotations`, type hints on public
  functions, `snake_case` functions/modules and `PascalCase` classes.
- Line length 120 (ruff config in `pyproject.toml`). The lint gate is
  intentionally minimal (`E4`, `E7`, `E9`, `F`) — do not reformat unrelated code.
- **Core stays core.** `httpx`, `pydantic`, `psutil`, `PyYAML`, `numpy` are the
  only hard dependencies. Anything else goes into an extra and must be
  **imported lazily**, inside the function that needs it, so the agent still
  starts (and `--cli` still works) without it.
- If you add an optional import, register it in `jarvis/dependencies.py` so the
  missing package produces a friendly `pip install -e ".[extra]"` message
  instead of a traceback.
- Never write secrets to a log: use `jarvis.logs.logger.get_logger`, which
  redacts API keys, bearer tokens and passwords automatically.

## 4. Security-sensitive changes

Anything touching `jarvis/security/`, `jarvis/tools/`, `jarvis/filesystem/`, or a
new tool handler must:

1. Go through `ToolRegistry.execute()` → `SecurityGate.authorize()` (no direct
   handler calls, no bypass).
2. Declare an honest `RiskLevel` on the `ToolSpec` and validate/coerce every
   parameter — assume the LLM will send wrong types.
3. Come with a test in `jarvis/tests/test_security_hardening.py`,
   `test_brain_robustness.py` or `test_prompt_injection.py`.
4. Keep the fail-closed invariant: no confirmation channel ⇒ deny.

## 5. Adding an evaluation case

- Tool-calling: add one line to `eval/tool_calling_cases.jsonl`. Every
  registered tool must have at least one case — `test_eval_harness.py` enforces it.
- Security: add one line to `eval/security_cases.jsonl` (include benign controls
  when you add a new rule, so false positives are visible).

## 6. Pull requests

- One logical change per PR; a clear commit message (`fix:`, `feat:`, `docs:`,
  `build:`, `ci:`, `eval:`, `test:`).
- State what you ran (`pytest`, `ruff`, which eval) in the PR description.
- Never claim a benchmark, screenshot or demo you did not produce — leave the
  README `Results` cells at `TBD` until a real run fills them.

## 7. License

By contributing you agree your work is licensed under the [MIT License](LICENSE).
