"""Entrypoint for JARVIS PERSONAL AI (`python -m jarvis` or `jarvis`)."""

from __future__ import annotations

import argparse
import json
import sys

from jarvis import __app_name__, __version__
from jarvis.app.cli import (
    cli_confirmation_prompt,
    format_doctor_report,
    run_interactive_cli,
    run_self_test,
)
from jarvis.app.controller import JarvisController
from jarvis.config.loader import load_config
from jarvis.interface import is_gui_available


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jarvis",
        description=f"{__app_name__} v{__version__} — Local Privacy-First Desktop Voice Operator",
    )
    parser.add_argument("--config", type=str, default=None, help="Path to custom config.yaml")
    parser.add_argument("--data-dir", type=str, default=None, help="Override ~/.jarvis data directory")
    parser.add_argument("--model", type=str, default=None, help="Preferred installed Ollama model name")
    parser.add_argument("--lang", choices=["en", "ta"], default=None, help="Active language (en=English, ta=Tamil)")
    parser.add_argument("--cli", action="store_true", help="Launch interactive terminal operator instead of GUI")
    parser.add_argument("--doctor", action="store_true", help="Inspect hardware, Ollama, voice & dependencies")
    parser.add_argument("--json", action="store_true", help="Output --doctor report as JSON")
    parser.add_argument("--self-test", action="store_true", help="Run non-destructive runtime verification suite")
    parser.add_argument("--once", type=str, default=None, help="Execute a single instruction and exit")
    parser.add_argument("--mute", action="store_true", help="Start with speech output muted")
    parser.add_argument("--version", action="version", version=f"{__app_name__} {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.self_test:
        return run_self_test(args.config)

    cfg = load_config(user_config_path=args.config, data_dir_override=args.data_dir)
    if args.model:
        cfg.ollama.preferred_model = args.model
    if args.lang:
        cfg.agent.active_language = args.lang
    if args.mute:
        cfg.tts.muted = True

    if args.doctor:
        ctrl = JarvisController(cfg, start_background_services=False)
        try:
            report = ctrl.run_environment_doctor()
            if args.json:
                print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
            else:
                print(format_doctor_report(report))
        finally:
            ctrl.shutdown()
        return 0

    if args.once:
        ctrl = JarvisController(
            cfg,
            confirmation_handler=cli_confirmation_prompt,
            start_background_services=False,
        )
        try:
            ctrl.refresh_ollama()
            res = ctrl.process_input_sync(
                args.once, source="cli_once", speak_response=not cfg.tts.muted
            )
            badge = "[VERIFIED]" if res.verified is True else f"[{res.status.upper()}]"
            print(f"JARVIS {badge}: {res.response}")
            return 0 if res.status in {"completed", "meta"} else 1
        finally:
            ctrl.shutdown()

    if args.cli or not is_gui_available():
        if not args.cli and not is_gui_available():
            print(
                "Note: PySide6 is not installed — launching interactive CLI mode.\n"
                "Install GUI support anytime with: pip install PySide6\n"
            )
        ctrl = JarvisController(cfg, start_background_services=True)
        return run_interactive_cli(ctrl)

    from jarvis.interface.main_window import launch_gui

    ctrl = JarvisController(cfg, start_background_services=True)
    return launch_gui(ctrl)


if __name__ == "__main__":
    raise SystemExit(main())
