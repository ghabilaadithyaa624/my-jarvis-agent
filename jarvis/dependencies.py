"""Optional-dependency map and friendly startup diagnostics.

JARVIS is deliberately split into a tiny core (``httpx``, ``pydantic``,
``psutil``, ``PyYAML``, ``numpy``) plus optional feature extras declared in
``pyproject.toml``. When the user launches the app without an optional package
that a requested feature needs, we must never dump a raw ``ModuleNotFoundError``
traceback at them — instead we explain which extra to install.
"""

from __future__ import annotations

from typing import Dict, Optional

#: Import name -> (extra name, short role description)
MODULE_TO_EXTRA: Dict[str, Dict[str, str]] = {
    "PySide6": {"extra": "gui", "role": "the PySide6 desktop HUD"},
    "faster_whisper": {"extra": "voice", "role": "offline speech-to-text (faster-whisper)"},
    "sounddevice": {"extra": "voice", "role": "microphone / speaker audio I/O"},
    "openwakeword": {"extra": "voice", "role": "the offline 'Hey Jarvis' wake word"},
    "piper": {"extra": "voice", "role": "Piper neural speech output"},
    "pyttsx3": {"extra": "voice", "role": "the offline SAPI5 voice fallback"},
    "pycaw": {"extra": "windows", "role": "Windows WASAPI volume control"},
    "comtypes": {"extra": "windows", "role": "Windows COM audio integration"},
    "win32api": {"extra": "windows", "role": "Windows Win32 integration"},
    "mss": {"extra": "windows", "role": "fast screenshot capture"},
    "PIL": {"extra": "windows", "role": "image capture & verification"},
    "send2trash": {"extra": "windows", "role": "Recycle-Bin-safe file deletion"},
    "docx": {"extra": "documents", "role": "reading Word (.docx) documents"},
    "pypdf": {"extra": "documents", "role": "reading PDF documents"},
    "openpyxl": {"extra": "documents", "role": "reading Excel (.xlsx) workbooks"},
    "playwright": {"extra": "browser", "role": "interactive browser automation"},
}

#: Core runtime imports — missing ones are a broken install, not an optional feature.
CORE_MODULES = frozenset({"httpx", "pydantic", "psutil", "yaml", "numpy"})

FULL_INSTALL_HINT = 'pip install -e ".[full]"'


def extra_for_module(module_name: Optional[str]) -> Optional[str]:
    """Return the pyproject extra that provides ``module_name`` (dotted names ok)."""
    if not module_name:
        return None
    root = module_name.split(".")[0]
    entry = MODULE_TO_EXTRA.get(root)
    return entry["extra"] if entry else None


def friendly_missing_dependency_message(module_name: Optional[str]) -> Optional[str]:
    """Build a calm, actionable install hint for a missing module.

    Returns ``None`` when the module is not part of JARVIS's dependency map so
    the caller can re-raise the original error untouched.
    """
    root = (module_name or "").split(".")[0]
    if not root:
        return None

    if root in CORE_MODULES:
        return (
            f"JARVIS core dependencies are missing: module '{module_name}'.\n\n"
            "JARVIS cannot run without its core runtime. Install it with:\n"
            f"    pip install -e .\n"
            'or, for the complete Windows 11 setup:\n'
            f"    {FULL_INSTALL_HINT}"
        )

    entry = MODULE_TO_EXTRA.get(root)
    if entry is None:
        return None

    return (
        f"JARVIS could not start because an optional component is missing: {entry['role']}\n"
        f"  missing Python module : {module_name}\n"
        f"  provided by the extra : {entry['extra']}\n\n"
        "Install that feature with:\n"
        f'    pip install -e ".[{entry["extra"]}]"\n'
        "or install every optional feature at once with:\n"
        f"    {FULL_INSTALL_HINT}\n\n"
        "The core agent still works without it:\n"
        "    python -m jarvis --cli      # terminal operator (no GUI/voice needed)\n"
        "    python -m jarvis --doctor   # see exactly which packages are missing"
    )
