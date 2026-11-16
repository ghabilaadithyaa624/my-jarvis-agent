"""Application controller, event bus & CLI package."""

from jarvis.app.events import AppEvent, EventBus, EventType

__all__ = [
    "AppEvent",
    "EventBus",
    "EventType",
    "JarvisController",
    "format_doctor_report",
    "run_interactive_cli",
    "run_self_test",
]


def __getattr__(name: str):
    if name == "JarvisController":
        from jarvis.app.controller import JarvisController

        return JarvisController
    if name in {"format_doctor_report", "run_interactive_cli", "run_self_test"}:
        from jarvis.app import cli

        return getattr(cli, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
