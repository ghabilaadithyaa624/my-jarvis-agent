"""Phase 9 Tests: Full JarvisController orchestration, fast-path commands & CLI entrypoints."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from jarvis.__main__ import main as jarvis_main
from jarvis.app.controller import JarvisController
from jarvis.interface import is_gui_available


def test_section14_voice_commands_integration(sandbox_config, mock_ollama):
    """Verify live controller handling of core Section 14 operator instructions."""
    _url, state = mock_ollama
    downloads = Path(sandbox_config.security.permitted_directories[2])
    (downloads / "lecture.pdf").write_text("pdf content")
    (downloads / "diagram.png").write_bytes(b"png content")

    ctrl = JarvisController(sandbox_config, start_background_services=False)
    try:
        ctrl.refresh_ollama()

        # 1. "Jarvis, check my laptop performance."
        state.scripted_replies.append("Your laptop is healthy with normal CPU and RAM usage.")
        r_perf = ctrl.process_input_sync("Jarvis, check my laptop performance.")
        assert r_perf.status == "completed" and r_perf.verified is True
        assert r_perf.tool_results[0].tool_name == "run_system_diagnostics"

        # 2. "Jarvis, explain what is currently running on my computer."
        state.scripted_replies.append("Here are the top active processes sorted by memory usage.")
        r_procs = ctrl.process_input_sync("Jarvis, explain what is currently running on my computer.")
        assert r_procs.status == "completed" and r_procs.verified is True
        assert r_procs.tool_results[0].tool_name == "list_running_processes"

        # 3. "Jarvis, organize my Downloads folder." (target sandbox Downloads)
        state.scripted_replies.extend([
            json.dumps({
                "thought": "Organize user's Downloads folder",
                "action": "organize_directory",
                "args": {"directory": str(downloads), "dry_run": False},
            }),
            json.dumps({
                "thought": "Verified 2 files sorted into Documents and Images.",
                "action": "final_answer",
                "answer": "Organized 2 files in Downloads into Documents and Images with zero overwrites.",
            }),
        ])
        r_org = ctrl.process_input_sync(f"Jarvis, organize folder {downloads}")
        assert r_org.status == "completed" and r_org.verified is True
        assert (downloads / "Documents" / "lecture.pdf").is_file()
        assert (downloads / "Images" / "diagram.png").is_file()

        # 4. Distinguish pure question from task execution (no unnecessary tool executed!)
        state.scripted_replies.append("Recursion is when a function calls itself with a smaller subproblem.")
        r_q = ctrl.process_input_sync("What is recursion in computer science?")
        assert r_q.status == "completed"
        assert r_q.verified is None
        assert r_q.tool_results == []

        # 5. Tamil language switch works cleanly
        assert ctrl.set_language("ta") is True
        assert ctrl.config.active_language_profile().code == "ta"

        # 6. Meta control: Emergency stop & reset
        r_em = ctrl.process_input_sync("emergency stop")
        assert r_em.status == "meta"
        assert ctrl.emergency.is_engaged is True
        ctrl.reset_emergency_stop()
        assert ctrl.emergency.is_engaged is False

        # Verify task history recorded all 3 computer tasks
        history = ctrl.utilities.get_task_history()
        assert len(history) == 3
        assert all(h["verified"] for h in history)
    finally:
        ctrl.shutdown()


def test_cli_doctor_selftest_and_once_modes(tmp_path: Path, capsys):
    data_dir = tmp_path / "cli_data"

    # --doctor --json
    code_doc = jarvis_main(["--data-dir", str(data_dir), "--doctor", "--json"])
    assert code_doc == 0
    doc_out = capsys.readouterr().out
    parsed = json.loads(doc_out)
    assert "hardware_and_os" in parsed and "ollama" in parsed

    # --once fast-path works even when local Ollama daemon is offline!
    code_once = jarvis_main(["--data-dir", str(data_dir), "--mute", "--once", "Jarvis, check disk space"])
    assert code_once == 0
    once_out = capsys.readouterr().out
    assert "[VERIFIED]" in once_out and ("disk space" in once_out.lower() or "c:" in once_out.lower())

    # --self-test
    code_st = jarvis_main(["--self-test"])
    assert code_st == 0
    st_out = capsys.readouterr().out
    assert "[PASS] Security CommandValidator blocks destructive shell" in st_out
    assert "[PASS] SHA-256 audit log chain integrity" in st_out


@pytest.mark.skipif(not is_gui_available(), reason="PySide6 not installed")
def test_pyside6_gui_hud_lifecycle_model_switch_and_controls(sandbox_config, mock_ollama):
    """Instantiate real PySide6 JARVIS HUD in offscreen mode and test every major control."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from jarvis.interface.main_window import JarvisMainWindow

    _url, state = mock_ollama
    state.scripted_replies.append("Your laptop CPU and memory are operating well within budget.")

    app = QApplication.instance() or QApplication([])
    ctrl = JarvisController(sandbox_config, start_background_services=False)
    win = JarvisMainWindow(ctrl)
    win.show()
    try:
        ctrl.refresh_ollama()
        app.processEvents()

        # 1. Verify Ollama models populated in dropdown & switch model via GUI
        assert win.model_combo.count() == 2  # qwen2.5:7b and qwen2.5:3b (embedding excluded!)
        assert "ONLINE" in win.ollama_pill.text() or "LOCAL" in win.ollama_pill.text()
        win.model_combo.setCurrentIndex(1)
        app.processEvents()
        assert ctrl.models.active_model == "qwen2.5:3b"
        assert "qwen2.5:7b" in state.unloaded_models

        # 2. Verify VoiceOrbWidget QPainter rendering across all visual states
        for orb_state in ("idle", "listening", "thinking", "executing", "speaking", "emergency"):
            win.orb.set_state(orb_state)
            win.orb.set_audio_level(0.75)
            pix = win.orb.grab()
            assert not pix.isNull()

        # 3. Submit a live instruction through the ConversationPanel input bar
        win.conv_panel.input_box.setText("Jarvis, check my laptop performance.")
        fut = ctrl.submit_input(win.conv_panel.input_box.text(), source="gui", speak_response=False)
        res = fut.result(timeout=15.0)
        assert res.verified is True
        for _ in range(5):
            app.processEvents()
            time.sleep(0.02)

        html_text = win.conv_panel.chat_view.toPlainText()
        assert "check my laptop performance" in html_text
        assert "[VERIFIED]" in html_text
        assert win.monitor_panel.tool_table.rowCount() >= 1
        assert win.monitor_panel.tool_table.item(0, 2).text() == "VERIFIED"

        # 4. Verify Mute, Emergency Stop & Reset buttons
        win._toggle_mute()
        assert ctrl.speech.is_muted is False  # started muted=True in fixture
        win._toggle_emergency()
        app.processEvents()
        assert ctrl.emergency.is_engaged is True
        assert "RESET" in win.emergency_btn.text()
        win._toggle_emergency()
        app.processEvents()
        assert ctrl.emergency.is_engaged is False

        # 5. Verify Memory, Permissions & Settings Environment Doctor panels
        win.mem_panel.fact_key.setText("gpu_model")
        win.mem_panel.fact_val.setText("NVIDIA RTX 3050 6GB")
        win.mem_panel._add_fact()
        assert win.mem_panel.facts_table.rowCount() == 1

        win.perm_panel.refresh()
        assert win.perm_panel.dir_list.count() >= 4
        assert win.perm_panel.audit_table.rowCount() >= 2

        win.settings_panel._run_doctor()
        assert "JARVIS PERSONAL AI" in win.settings_panel.doctor_box.toPlainText()
    finally:
        win.close()
        app.processEvents()
