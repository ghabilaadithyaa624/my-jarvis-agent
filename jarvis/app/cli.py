"""Interactive CLI Operator, Environment Doctor & Phased Self-Test Runner."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from jarvis.app.controller import JarvisController
from jarvis.config.loader import load_config
from jarvis.security.policy import ConfirmationRequest


def cli_confirmation_prompt(req: ConfirmationRequest) -> bool:
    """Interactive terminal confirmation gate for sensitive desktop operations."""
    print("\n" + "=" * 68)
    print(f"  [SECURITY CONFIRMATION REQUIRED — Risk: {req.risk_level.value}]")
    print(f"  Tool   : {req.tool_name}")
    print(f"  Target : {req.target_summary}")
    print(f"  Reason : {req.reason}")
    print("=" * 68)
    if not sys.stdin or not sys.stdin.isatty():
        print("  Non-interactive stdin detected -> DENIED automatically (fail-closed).")
        return False
    try:
        ans = input("  Allow this action once? [y/N]: ").strip().lower()
        return ans in {"y", "yes"}
    except (EOFError, KeyboardInterrupt):
        return False


def format_doctor_report(report: Dict[str, Any]) -> str:
    """Render a clean, readable environment readiness report."""
    hw = report["hardware_and_os"]
    gpu = hw["gpu"]
    ol = report["ollama"]
    tts = report["tts_assets"]
    lines: List[str] = [
        "=" * 74,
        "  JARVIS PERSONAL AI — ENVIRONMENT & HARDWARE ASSESSMENT",
        "=" * 74,
        f"  OS             : {hw['os']} ({hw['os_version']})",
        f"  Windows 11     : {'Yes' if report['is_windows_11'] else 'No (compatibility mode)'}",
        f"  Python         : {hw['python_version']} ({hw['architecture']})",
        f"  CPU            : {hw['cpu_model']} ({hw['cpu_physical_cores']}C / {hw['cpu_logical_threads']}T)",
        f"  RAM            : {hw['ram_total_gb']} GB",
        f"  GPU            : {gpu['name']}"
        + (
            f" ({gpu['vram_free_mb']} MB free / {gpu['vram_total_mb']} MB VRAM, Driver {gpu['driver_version']})"
            if gpu["available"]
            else " (nvidia-smi not detected)"
        ),
        f"  Data Directory : {report['data_dir']}",
        "-" * 74,
        "  LOCAL OLLAMA STATUS",
        f"  Endpoint       : {ol['base_url']} (strictly local loopback)",
        f"  Daemon Status  : {'ONLINE (v' + str(ol.get('version') or '?') + ')' if ol['online'] else 'OFFLINE'}",
        f"  Active Model   : {ol['active_model'] or 'None'}",
        f"  Local Models   : {', '.join(m['name'] + ' [' + m['vram_fit'] + ']' for m in ol['compatible_models']) or 'None detected'}",
        "-" * 74,
        "  VOICE & SPEECH STATUS",
        f"  Piper Voices   : {', '.join(tts['installed_voice_models']) or 'None in voices folder'}",
        f"  Windows SAPI5  : {'Available (offline fallback)' if tts['sapi5_available'] else 'N/A (non-Windows)'}",
        f"  Microphones    : {', '.join(report['audio_devices']['inputs'][:3]) or report['audio_devices']['note']}",
        "-" * 74,
        "  DISCOVERED DESKTOP TOOLS",
    ]
    for name, path in report["external_binaries"].items():
        lines.append(f"  {name:<14} : {path or 'not found'}")

    lines.append("-" * 74)
    lines.append("  DEPENDENCIES")
    for pkg, info in report["packages"].items():
        mark = "[OK]" if info.get("installed") else "[--]"
        ver = f"v{info['version']}" if info.get("version") else info.get("status", "not installed")
        lines.append(f"  {mark} {pkg:<16} {ver:<18} ({info['role']})")

    if report["action_items"]:
        lines.append("-" * 74)
        lines.append("  RECOMMENDED NEXT STEPS")
        for idx, item in enumerate(report["action_items"], start=1):
            lines.append(f"  {idx}. {item}")

    lines.append("=" * 74)
    return "\n".join(lines)


def run_self_test(config_path: str | None = None) -> int:
    """Execute live non-destructive verification checks on the user's machine."""
    from jarvis.app.controller import JarvisController

    cfg = load_config(config_path)
    ctrl = JarvisController(cfg, start_background_services=False)
    checks: List[tuple[str, bool, str]] = []
    try:
        # 1. Config & Loopback Guard
        checks.append(("Local-only Ollama enforcement", cfg.ollama.enforce_local_only, cfg.ollama.base_url))

        # 2. Ollama connectivity (honest pass/warn)
        ok_ol, msg_ol = ctrl.models.verify_ready()
        checks.append(("Ollama daemon & installed model", ok_ol, msg_ol))

        # 3. System telemetry
        diag = ctrl.tools.execute("run_system_diagnostics", {})
        checks.append((
            "System diagnostics & telemetry",
            diag.success and diag.verified,
            f"Health={diag.data.get('overall_health')}, CPU={diag.data.get('snapshot', {}).get('cpu_percent')}%, RAM={diag.data.get('snapshot', {}).get('ram_percent')}%",
        ))

        # 4. Security validator (destructive command must be blocked)
        blocked = ctrl.tools.execute("run_terminal_command", {"command": "rm -rf /"})
        checks.append((
            "Security CommandValidator blocks destructive shell",
            (not blocked.success) and blocked.permission_denied,
            blocked.error or "",
        ))

        # 5. Memory persistence roundtrip
        ctrl.knowledge.remember("_selftest_probe", "verified_ok", category="system")
        recalled = ctrl.knowledge.recall("_selftest_probe")
        mem_ok = any(r["value"] == "verified_ok" for r in recalled)
        for r in recalled:
            if r["key"] == "_selftest_probe":
                ctrl.knowledge.delete(r["id"])
        checks.append(("SQLite persistent memory roundtrip", mem_ok, f"FTS5={ctrl.db.has_fts5}"))

        # 6. Audit chain integrity
        chain_ok, entries, chain_msg = ctrl.audit.verify_chain()
        checks.append(("SHA-256 audit log chain integrity", chain_ok, chain_msg))

        # 7. Speech sanitizer code-block suppression
        spoken = ctrl.speech.sanitizer.sanitize("Done.\n```python\nprint('secret')\n```")
        checks.append((
            "SpeechSanitizer suppresses code blocks",
            "print" not in spoken and "conversation panel" in spoken,
            spoken,
        ))

        # 8. Voice & TTS availability check (informational)
        stt_ok, stt_msg = ctrl.stt.check_availability()
        tts_backend, tts_msg = ctrl.speech.detect_backend()
        checks.append(("Local STT engine installed", stt_ok, stt_msg))
        checks.append(("Local TTS engine available", tts_backend not in {"unavailable", "disabled"}, tts_msg))

    finally:
        ctrl.shutdown()

    print("=" * 74)
    print("  JARVIS PERSONAL AI — RUNTIME SELF-TEST VERIFICATION")
    print("=" * 74)
    core_failed = 0
    for idx, (name, passed, detail) in enumerate(checks):
        badge = "PASS" if passed else ("WARN" if idx in {1, 7, 8} else "FAIL")
        if badge == "FAIL":
            core_failed += 1
        print(f"  [{badge}] {name}")
        print(f"         -> {detail}")
    print("=" * 74)
    return 1 if core_failed else 0


def run_interactive_cli(controller: JarvisController) -> int:
    """Run JARVIS as a full interactive terminal operator."""
    controller.security.set_confirmation_handler(cli_confirmation_prompt)
    status = controller.refresh_ollama()
    print("=" * 70)
    print("  JARVIS PERSONAL AI — Interactive Operator Console")
    print(f"  Ollama : {'ONLINE' if status['online'] else 'OFFLINE'} | Model: {status['active_model'] or 'none'}")
    print("  Commands: /models, /model <name>, /listen, /stop, /doctor, /emergency, /reset, /quit")
    print("=" * 70)

    try:
        while True:
            try:
                user_in = input("\nYou > ").strip()
            except EOFError:
                break
            if not user_in:
                continue
            if user_in.lower() in {"/quit", "/exit", "exit", "quit"}:
                break
            if user_in.lower() == "/models":
                st = controller.refresh_ollama()
                for m in st["compatible_models"]:
                    mark = "*" if m["name"] == st["active_model"] else " "
                    print(f"  {mark} {m['name']} ({m['size_gb']} GB, {m['vram_fit']}) — {m['notes']}")
                if not st["compatible_models"]:
                    print("  No compatible local Ollama models detected.")
                continue
            if user_in.lower().startswith("/model "):
                target = user_in[7:].strip()
                try:
                    info = controller.switch_model(target)
                    print(f"  Switched to local model: {info['name']} ({info['vram_fit']})")
                except Exception as exc:
                    print(f"  Model switch failed: {exc}")
                continue
            if user_in.lower() == "/listen":
                ok, msg = controller.start_listening()
                print(f"  {msg}")
                continue
            if user_in.lower() == "/stop":
                controller.stop_listening()
                controller.cancel_current_task()
                print("  Stopped listening and cancelled active tasks.")
                continue
            if user_in.lower() == "/emergency":
                info = controller.trigger_emergency_stop("CLI /emergency command")
                print(f"  EMERGENCY STOP ENGAGED: {json.dumps(info)}")
                continue
            if user_in.lower() == "/reset":
                controller.reset_emergency_stop()
                print("  Emergency Stop reset.")
                continue
            if user_in.lower() == "/doctor":
                print(format_doctor_report(controller.run_environment_doctor()))
                continue

            res = controller.process_input_sync(
                user_in, source="cli", speak_response=not controller.speech.is_muted
            )
            ver_tag = ""
            if res.verified is True:
                ver_tag = " [VERIFIED]"
            elif res.verified is False:
                ver_tag = f" [{res.status.upper()}]"
            print(f"\nJARVIS{ver_tag} > {res.response}")
    except KeyboardInterrupt:
        print("\nInterrupted — shutting down cleanly.")
    finally:
        controller.shutdown()
    return 0
