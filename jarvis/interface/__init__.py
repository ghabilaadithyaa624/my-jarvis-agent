"""PySide6 Desktop HUD interface package (lazily imported so CLI works without Qt)."""

from __future__ import annotations

import importlib.util


def is_gui_available() -> bool:
    if importlib.util.find_spec("PySide6") is None:
        return False
    try:
        import PySide6.QtWidgets  # noqa: F401
        return True
    except Exception:
        return False


__all__ = ["is_gui_available"]
