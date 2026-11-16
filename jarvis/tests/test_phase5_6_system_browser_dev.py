"""Phase 5 & 6 Tests: System telemetry, safe process control, browser & developer automation."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from jarvis.automation.app_controller import AppController
from jarvis.automation.developer import DeveloperAutomation
from jarvis.browser.controller import BrowserController, normalize_http_url
from jarvis.config.schema import BrowserConfig
from jarvis.filesystem.document_reader import DocumentReader
from jarvis.filesystem.operations import FileSystemManager
from jarvis.security.emergency import EmergencyStopController
from jarvis.system.controls import SystemController
from jarvis.system.monitor import SystemMonitor


def test_system_specs_diagnostics_and_settings_uri_resolver(tmp_path: Path):
    mon = SystemMonitor()
    specs = mon.get_system_specs()
    assert specs["cpu_logical_threads"] >= 1
    assert specs["ram_total_gb"] > 0

    diag = mon.run_diagnostics()
    assert diag["overall_health"] in {"HEALTHY", "ATTENTION", "DEGRADED"}
    assert len(diag["top_memory_processes"]) >= 1

    sc = SystemController(tmp_path / "shots")
    page, uri = sc.resolve_settings_uri("open bluetooth")
    assert uri == "ms-settings:bluetooth"
    _, gpu_uri = sc.resolve_settings_uri("graphics")
    assert gpu_uri == "ms-settings:display-advancedgraphics"


def test_protected_process_immunity_and_verified_child_stop(tmp_path: Path):
    sc = SystemController(tmp_path / "shots")

    # Must NEVER stop Windows critical processes, Ollama, or self PID
    assert sc.is_protected_process("lsass.exe", 999)[0] is True
    assert sc.is_protected_process("ollama.exe", 1234)[0] is True
    assert sc.is_protected_process("explorer.exe", 2345)[0] is True
    self_attempt = sc.stop_process(str(os.getpid()))
    assert self_attempt["success"] is False
    assert len(self_attempt["blocked"]) == 1

    # Spawn a harmless sleeper process and verify clean graceful stop
    dummy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        res = sc.stop_process(str(dummy.pid))
        assert res["success"] is True
        assert res["verified"] is True
        assert any(s["pid"] == dummy.pid for s in res["stopped"])
    finally:
        if dummy.poll() is None:
            dummy.kill()

    # Unnecessary process scan succeeds without killing anything
    scan = sc.find_unnecessary_processes(min_memory_mb=10.0)
    assert "candidates" in scan and scan["protected_processes_skipped"] >= 1


def test_browser_url_safety_and_search_builder():
    bc = BrowserController(BrowserConfig())
    assert normalize_http_url("docs.python.org") == "https://docs.python.org"
    assert normalize_http_url("https://ollama.com") == "https://ollama.com"

    with pytest.raises(ValueError, match="Disallowed URL scheme"):
        normalize_http_url("javascript:alert(1)")
    with pytest.raises(ValueError, match="Disallowed URL scheme"):
        normalize_http_url("file:///C:/Windows/System32/config/SAM")

    search_url = bc.build_search_url("faster whisper int8 windows", engine="duckduckgo")
    assert search_url.startswith("https://duckduckgo.com/?q=faster+whisper")


def test_developer_python_execution_traceback_logs_and_projects(tmp_path: Path):
    ws = tmp_path / "workspace"
    projects = tmp_path / "Projects"
    (projects / "my_api").mkdir(parents=True)
    (projects / "my_api" / "pyproject.toml").write_text("[project]\nname='my_api'\n")
    (projects / "edu_scene.blend").write_bytes(b"BLENDER-v402" + b"\x00" * 64)

    fs = FileSystemManager(lambda: [ws, projects])
    dev = DeveloperAutomation(
        app_controller=AppController(),
        fs_manager=fs,
        doc_reader=DocumentReader(),
        monitor=SystemMonitor(),
        emergency=EmergencyStopController(),
        workspace_dir=ws,
    )

    # 1. Execute valid Python script & verify stdout
    good_py = ws / "calc.py"
    good_py.write_text("print(sum(range(1, 11)))\n", encoding="utf-8")
    r_ok = dev.run_python_script(str(good_py))
    assert r_ok["success"] and r_ok["verified"]
    assert r_ok["exit_code"] == 0
    assert r_ok["stdout"].strip() == "55"

    # 2. Execute buggy Python script & extract structured traceback for debugging
    bad_py = ws / "buggy.py"
    bad_py.write_text("x = 10\ny = 0\nprint(x / y)\n", encoding="utf-8")
    r_bad = dev.run_python_script(str(bad_py))
    assert r_bad["success"] is False and r_bad["verified"] is True
    assert "ZeroDivisionError" in r_bad["traceback_summary"]["exception_line"]
    assert r_bad["traceback_summary"]["frames"][-1]["line"] == 3

    # 3. Run safe terminal command (quote executable in case path has spaces)
    cmd_res = dev.run_terminal_command(f'"{sys.executable}" --version')
    assert cmd_res["success"] and cmd_res["verified"]
    assert "Python" in (cmd_res["stdout"] + cmd_res["stderr"])

    # 4. Error log analysis
    log_file = ws / "server.log"
    log_file.write_text(
        "INFO Starting app\nINFO Connected\nERROR Database timeout on port 5432\n"
        '  File "app/db.py", line 48, in connect\nRuntimeError: pool exhausted\n',
        encoding="utf-8",
    )
    log_res = dev.analyze_error_log(str(log_file))
    assert log_res["success"] and log_res["error_count"] == 2
    assert log_res["referenced_source_locations"][0] == {"file": "app/db.py", "line": 48}

    # 5. Find Blender & Python projects
    found_blend = dev.find_projects("Blender edu project", project_type="blender")
    assert found_blend["count"] == 1
    assert found_blend["projects"][0]["name"] == "edu_scene.blend"

    found_py = dev.find_projects("my_api", project_type="python")
    assert found_py["count"] == 1
