"""Phase 2 Tests: Config persistence, EventBus, Conversation, Knowledge & Personal Utilities."""

from __future__ import annotations

import time
from pathlib import Path

from jarvis.app.events import EventBus, EventType
from jarvis.config.loader import load_config, save_config
from jarvis.memory.conversation_store import ConversationStore
from jarvis.memory.database import LocalDatabase
from jarvis.memory.knowledge_store import KnowledgeStore
from jarvis.memory.utilities_store import UtilitiesStore


def test_config_roundtrip_and_corrupt_backup(tmp_path: Path):
    data_dir = tmp_path / "cfg_data"
    cfg = load_config(data_dir_override=data_dir)
    cfg.tts.speed = 1.15
    cfg.agent.active_language = "ta"
    saved = save_config(cfg)
    assert saved.is_file()

    reloaded = load_config(data_dir_override=data_dir)
    assert reloaded.tts.speed == 1.15
    assert reloaded.agent.active_language == "ta"
    assert reloaded.active_language_profile().whisper_code == "ta"

    # Corrupt user config must be backed up, never silently destroyed or crash startup
    saved.write_text(":::invalid: yaml: [[[", encoding="utf-8")
    recovered = load_config(data_dir_override=data_dir)
    assert recovered.ollama.num_ctx == 4096
    backups = list(data_dir.glob("config.corrupt.*.yaml"))
    assert len(backups) == 1


def test_event_bus_subscribe_emit_and_unsubscribe():
    bus = EventBus()
    received = []
    unsub = bus.subscribe(EventType.MODEL_CHANGED, lambda ev: received.append(ev.payload["name"]))
    bus.emit(EventType.MODEL_CHANGED, name="qwen2.5:7b")
    unsub()
    bus.emit(EventType.MODEL_CHANGED, name="llama3.1:8b")
    assert received == ["qwen2.5:7b"]


def test_conversation_and_knowledge_memory(tmp_path: Path):
    db = LocalDatabase(tmp_path / "mem.db")
    try:
        conv = ConversationStore(db)
        conv.add_message("user", "Open my Blender solar system project")
        conv.add_message("assistant", "Opened SolarSystem.blend in Blender.", model="qwen2.5:7b", verified=True)

        sessions = conv.list_sessions()
        assert sessions[0]["title"].startswith("Open my Blender")

        ctx = conv.build_llm_context(max_messages=5)
        assert len(ctx) == 2
        assert ctx[0]["role"] == "user"

        know = KnowledgeStore(db)
        know.remember("blender_edu_project", r"D:\Projects\SolarSystem.blend", category="project", pinned=True)
        know.remember("preferred_editor", "Visual Studio Code", category="preference")

        hits = know.recall("SolarSystem")
        assert len(hits) >= 1
        assert hits[0]["key"] == "blender_edu_project"

        rel = know.get_pinned_and_relevant("open editor")
        keys = {r["key"] for r in rel}
        assert "blender_edu_project" in keys  # pinned
        assert "preferred_editor" in keys     # query match
    finally:
        db.close()


def test_personal_utilities_notes_todos_reminders_timers_and_history(tmp_path: Path):
    db = LocalDatabase(tmp_path / "util.db")
    fired_reminders = []
    fired_timers = []
    util = UtilitiesStore(
        db,
        on_reminder=fired_reminders.append,
        on_timer=fired_timers.append,
    )
    try:
        # Notes
        note = util.save_note("GPU Tuning", "Keep faster-whisper on CPU int8", tags="hardware,rtx3050")
        assert note["id"] > 0
        assert len(util.search_notes("rtx3050")) == 1

        # To-dos
        td = util.add_todo("Finish Blender lighting pass", priority="high")
        assert len(util.list_todos("pending")) == 1
        updated = util.update_todo(td["id"], "completed")
        assert updated is not None and updated["status"] == "completed"
        assert len(util.list_todos("pending")) == 0

        # Reminders (relative time trigger)
        util.create_reminder("Stand up and stretch", "in 0 seconds")
        due = util.check_due_reminders()
        assert len(due) == 1
        assert len(fired_reminders) == 1

        # Timers (start, list, fire, cancel)
        util.start_timer(1.0, label="Tea")
        t2 = util.start_timer(60.0, label="Pomodoro")
        assert len(util.list_timers()) == 2
        assert util.cancel_timer(t2["id"]) is True
        time.sleep(1.05)
        util._check_timers()
        assert any(t["label"] == "Tea" for t in fired_timers)

        # Task history
        util.log_task(
            session_id="s1",
            user_input="check disk space",
            intent="system_query",
            status="completed",
            verified=True,
            steps_count=1,
            duration_ms=25.4,
            summary="NVMe has 280 GB free.",
        )
        hist = util.get_task_history()
        assert len(hist) == 1 and hist[0]["verified"] is True
    finally:
        util.stop_scheduler()
        db.close()
