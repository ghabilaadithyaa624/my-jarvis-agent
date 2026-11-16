"""Optional-dependency guard: missing extras must produce advice, not tracebacks."""

from __future__ import annotations

import importlib

from jarvis.dependencies import (
    CORE_MODULES,
    MODULE_TO_EXTRA,
    extra_for_module,
    friendly_missing_dependency_message,
)


def test_extra_lookup_by_module_name():
    assert extra_for_module("PySide6") == "gui"
    assert extra_for_module("PySide6.QtWidgets") == "gui"
    assert extra_for_module("faster_whisper") == "voice"
    assert extra_for_module("playwright.sync_api") == "browser"
    assert extra_for_module("openpyxl") == "documents"
    assert extra_for_module("pycaw") == "windows"
    assert extra_for_module("sqlite3") is None
    assert extra_for_module(None) is None


def test_optional_package_message_names_the_extra():
    message = friendly_missing_dependency_message("PySide6")
    assert message is not None
    assert 'pip install -e ".[gui]"' in message
    assert "full" in message
    assert "--cli" in message


def test_core_package_message_points_at_core_install():
    message = friendly_missing_dependency_message("httpx")
    assert message is not None
    assert "core dependencies are missing" in message
    assert "pip install -e ." in message


def test_unknown_module_returns_none_so_real_errors_propagate():
    assert friendly_missing_dependency_message("some_third_party_thing") is None
    assert friendly_missing_dependency_message("") is None


def test_every_mapped_module_has_an_extra_and_role():
    for module, entry in MODULE_TO_EXTRA.items():
        assert entry["extra"] in {"gui", "voice", "windows", "documents", "browser"}, module
        assert entry["role"], module


def test_main_reports_missing_optional_package_without_traceback(monkeypatch, capsys):
    """`python -m jarvis` must print guidance and exit 2, not raise."""
    import jarvis.__main__ as cli

    def _boom(*_args, **_kwargs):
        raise ModuleNotFoundError("No module named 'PySide6'", name="PySide6")

    monkeypatch.setattr(cli, "load_config", _boom)
    code = cli.main(["--doctor"])
    captured = capsys.readouterr()
    assert code == 2
    assert "PySide6" in captured.err
    assert 'pip install -e ".[gui]"' in captured.err
    assert "Traceback" not in captured.err


def test_core_install_is_enough_to_import_the_agent():
    """Core dependencies alone must be able to import the whole agent stack."""
    for module in CORE_MODULES:
        assert importlib.import_module(module) is not None

    # The controller pulls in every subsystem; all optional imports inside it
    # (PySide6, pycaw, faster-whisper, mss, playwright, ...) must be lazy.
    assert importlib.import_module("jarvis.app.controller") is not None
