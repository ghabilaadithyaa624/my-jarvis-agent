"""Master JarvisController orchestrating LLM, Voice, Speech, Brain, Tools, Memory & Security."""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from jarvis.app.events import EventBus, EventType
from jarvis.automation.app_controller import AppController
from jarvis.automation.app_registry import AppRegistry
from jarvis.automation.developer import DeveloperAutomation
from jarvis.brain.executor import AgentExecutor, AgentTurnResult
from jarvis.browser.controller import BrowserController
from jarvis.config.loader import ensure_runtime_directories, load_config, save_config
from jarvis.config.schema import JarvisConfig
from jarvis.filesystem.document_reader import DocumentReader
from jarvis.filesystem.operations import FileSystemManager
from jarvis.filesystem.organizer import DirectoryOrganizer, DuplicateFinder
from jarvis.logs.logger import AuditLogger, get_logger, setup_logging
from jarvis.memory.conversation_store import ConversationStore
from jarvis.memory.database import LocalDatabase
from jarvis.memory.knowledge_store import KnowledgeStore
from jarvis.memory.utilities_store import UtilitiesStore
from jarvis.models.model_manager import ModelManager
from jarvis.models.ollama_client import OllamaClient
from jarvis.security.emergency import EmergencyStopController
from jarvis.security.gate import ConfirmationHandler, SecurityGate
from jarvis.speech.tts_engine import SpeechEngine
from jarvis.system.controls import SystemController
from jarvis.system.env_probe import inspect_environment
from jarvis.system.monitor import SystemMonitor
from jarvis.system.resource_guard import ResourceGuard
from jarvis.tools.builtin_tools import register_builtin_tools
from jarvis.tools.registry import ToolRegistry
from jarvis.voice.listener import VoiceListener
from jarvis.voice.stt_engine import SpeechToTextEngine

log = get_logger("app.controller")


class JarvisController:
    """Lifecycle and concurrency manager for the entire JARVIS Personal AI runtime."""

    def __init__(
        self,
        config: Optional[JarvisConfig] = None,
        *,
        ollama_client: Optional[OllamaClient] = None,
        confirmation_handler: Optional[ConfirmationHandler] = None,
        start_background_services: bool = True,
    ) -> None:
        self.config = config or load_config()
        self.data_dir: Path = ensure_runtime_directories(self.config)
        setup_logging(self.data_dir / "logs")

        self.events = EventBus()
        self.audit = AuditLogger(self.data_dir / "logs" / "audit.jsonl")
        self.emergency = EmergencyStopController()

        # Security Gate
        self.security = SecurityGate(
            security_cfg=self.config.security,
            audit_logger=self.audit,
            emergency=self.emergency,
            confirmation_handler=confirmation_handler,
            on_audit_callback=lambda entry: self.events.emit(EventType.AUDIT_ENTRY, entry=entry),
            workspace_dir=self.data_dir / "workspace",
        )

        # Hardware Monitor & RTX 3050 6GB Resource Guard
        self.monitor = SystemMonitor()
        self.resource_guard = ResourceGuard(
            self.config.resources,
            self.config.ollama,
            self.config.stt,
            self.monitor,
        )
        self.sys_controls = SystemController(self.data_dir / "screenshots")

        # Exclusive Local Ollama Engine
        self.models = ModelManager(
            self.config.ollama,
            self.config.resources,
            client=ollama_client,
        )

        # Persistent Local SQLite Memory & Personal Utilities
        self.db = LocalDatabase(self.data_dir / "memory" / "jarvis.db")
        self.conversations = ConversationStore(self.db)
        self.knowledge = KnowledgeStore(self.db)
        self.utilities = UtilitiesStore(
            self.db,
            on_reminder=self._on_reminder_due,
            on_timer=self._on_timer_finished,
        )

        # Filesystem, Desktop Automation, Browser & Developer Specialists
        self.fs = FileSystemManager(
            permitted_roots_provider=self.security.path_guard.permitted_roots,
            max_results=self.config.security.max_search_results,
        )
        self.doc_reader = DocumentReader(max_bytes=self.config.security.max_file_read_bytes)
        self.organizer = DirectoryOrganizer()
        self.duplicates = DuplicateFinder()
        self.app_registry = AppRegistry()
        self.app_controller = AppController(self.app_registry)
        self.browser = BrowserController(self.config.browser)
        self.developer = DeveloperAutomation(
            app_controller=self.app_controller,
            fs_manager=self.fs,
            doc_reader=self.doc_reader,
            monitor=self.monitor,
            emergency=self.emergency,
            workspace_dir=self.data_dir / "workspace",
        )

        # Validated Tool Registry (all 41 tools)
        self.tools = ToolRegistry(self.security)
        register_builtin_tools(
            self.tools,
            apps=self.app_controller,
            fs=self.fs,
            reader=self.doc_reader,
            organizer=self.organizer,
            duplicates=self.duplicates,
            monitor=self.monitor,
            sys_ctrl=self.sys_controls,
            browser=self.browser,
            dev=self.developer,
            utilities=self.utilities,
            knowledge=self.knowledge,
            workspace_dir=self.data_dir / "workspace",
        )

        # Local Speech Output (Piper Neural TTS + offline Windows SAPI5 fallback)
        lang_profile = self.config.active_language_profile()
        self.speech = SpeechEngine(
            tts_cfg=self.config.tts,
            language_profile=lang_profile,
            on_state_change=lambda speaking, txt: self.events.emit(
                EventType.TTS_STATE, speaking=speaking, text=txt
            ),
            on_finished_utterance=lambda: self.voice.open_followup_window(),
        )

        # Local Voice Input (AdaptiveVAD + openWakeWord + faster-whisper CPU int8)
        self.stt = SpeechToTextEngine(
            stt_cfg=self.config.stt,
            resource_cfg=self.config.resources,
            device_resolver=self.resource_guard.resolve_stt_device,
        )
        self.voice = VoiceListener(
            stt_cfg=self.config.stt,
            stt_engine=self.stt,
            language_profile=lang_profile,
            on_transcript=self._on_voice_transcript,
            on_state_change=lambda st, detail: self.events.emit(
                EventType.VOICE_STATE, state=st, detail=detail
            ),
            on_audio_level=lambda lvl: self.events.emit(EventType.AUDIO_LEVEL, level=lvl),
            on_barge_in=self.interrupt_speech,
            is_tts_speaking=lambda: self.speech.is_speaking,
        )

        # Register Emergency Stop hooks to halt speech, mic & browser immediately
        self.emergency.register_hook(self._on_emergency_triggered)

        # 9-Stage Autonomous Agent Brain
        self.agent = AgentExecutor(
            config=self.config,
            model_manager=self.models,
            tool_registry=self.tools,
            conversations=self.conversations,
            knowledge=self.knowledge,
            utilities=self.utilities,
            emergency=self.emergency,
            resource_guard=self.resource_guard,
            event_bus=self.events,
            meta_handler=self._handle_meta_command,
        )

        # Non-blocking background task executor (serializes agent turns so GUI stays 60fps responsive)
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="jarvis-agent")
        self._telemetry_stop = threading.Event()
        self._telemetry_thread: Optional[threading.Thread] = None

        if start_background_services:
            self.start_services()

    # ---------------- Lifecycle ----------------

    def start_services(self) -> None:
        self.utilities.start_scheduler()
        self.refresh_ollama_async()
        if self._telemetry_thread is None or not self._telemetry_thread.is_alive():
            self._telemetry_stop.clear()
            self._telemetry_thread = threading.Thread(
                target=self._telemetry_loop,
                name="jarvis-telemetry",
                daemon=True,
            )
            self._telemetry_thread.start()

    def shutdown(self) -> None:
        log.info("Shutting down JARVIS Personal AI...")
        self._telemetry_stop.set()
        self.emergency.cancel_all_tasks("Application shutting down.")
        self.voice.stop()
        self.speech.shutdown()
        self.utilities.stop_scheduler()
        self.browser.close()
        self.stt.unload()
        self._executor.shutdown(wait=False, cancel_futures=True)
        self.db.close()

    def _telemetry_loop(self) -> None:
        interval = max(1.5, float(self.config.resources.telemetry_interval_sec))
        while not self._telemetry_stop.wait(timeout=interval):
            try:
                snap = self.monitor.snapshot(sample_interval=0.0, include_disks=False)
                self.events.emit(EventType.TELEMETRY_UPDATE, snapshot=snap.to_dict())
            except Exception:
                pass

    # ---------------- Ollama Model Controls ----------------

    def refresh_ollama(self) -> Dict[str, Any]:
        self.models.refresh_models()
        status = self.models.get_status_snapshot()
        self.events.emit(EventType.OLLAMA_STATUS, **status)
        self.events.emit(
            EventType.MODELS_UPDATED,
            models=status["compatible_models"],
            active_model=status["active_model"],
        )
        return status

    def refresh_ollama_async(self) -> None:
        threading.Thread(target=self.refresh_ollama, name="jarvis-ollama-probe", daemon=True).start()

    def switch_model(self, model_name: str) -> Dict[str, Any]:
        info = self.models.select_model(model_name)
        save_config(self.config)
        self.events.emit(EventType.MODEL_CHANGED, model=info.to_dict())
        return info.to_dict()

    # ---------------- Language & Voice Controls ----------------

    def set_language(self, lang_code: str) -> bool:
        if lang_code not in self.config.languages:
            return False
        self.config.agent.active_language = lang_code
        profile = self.config.active_language_profile()
        self.voice.set_language(profile)
        self.speech.set_language(profile)
        save_config(self.config)
        return True

    def start_listening(self, mode: Optional[str] = None) -> Tuple[bool, str]:
        if self.emergency.is_engaged:
            return False, "Emergency Stop is active. Reset Emergency Stop before enabling listening."
        if mode:
            self.voice.set_mode(mode)
        return self.voice.start()

    def stop_listening(self) -> None:
        self.voice.stop()

    def toggle_mute(self, muted: Optional[bool] = None) -> bool:
        new_state = (not self.speech.is_muted) if muted is None else bool(muted)
        self.speech.set_muted(new_state)
        save_config(self.config)
        return new_state

    def interrupt_speech(self) -> None:
        self.speech.stop()

    def _on_voice_transcript(self, transcript: str, detected_lang: str) -> None:
        self.events.emit(EventType.TRANSCRIPT_READY, text=transcript, language=detected_lang)
        self.submit_input(transcript, source="voice", speak_response=True)

    # ---------------- Task Execution & Safety Controls ----------------

    def process_input_sync(
        self,
        text: str,
        *,
        source: str = "text",
        speak_response: bool = False,
    ) -> AgentTurnResult:
        """Execute an instruction synchronously (used in CLI & automated tests)."""
        self.speech.stop()
        result = self.agent.handle_input(text, source=source)
        if speak_response and result.response and not self.speech.is_muted:
            self.speech.speak(result.response, language=result.language)
        return result

    def submit_input(
        self,
        text: str,
        *,
        source: str = "gui",
        speak_response: Optional[bool] = None,
        on_done: Optional[Callable[[AgentTurnResult], None]] = None,
    ) -> Future:
        """Execute an instruction on a background thread (keeps PySide6 UI silky smooth)."""
        should_speak = (not self.speech.is_muted) if speak_response is None else speak_response

        def _job() -> AgentTurnResult:
            res = self.process_input_sync(text, source=source, speak_response=should_speak)
            if on_done:
                try:
                    on_done(res)
                except Exception:
                    pass
            return res

        return self._executor.submit(_job)

    def cancel_current_task(self) -> int:
        self.speech.stop()
        count = self.emergency.cancel_all_tasks("Task cancelled by user.")
        self.audit.record("TASK_CONTROL", "cancel_task", "CANCELLED", "SAFE", {"cancelled_count": count})
        self.events.emit(EventType.TASK_STAGE, stage_number=0, stage="cancelled", detail="Task cancelled by user")
        return count

    def trigger_emergency_stop(self, reason: str = "User pressed Emergency Stop") -> Dict[str, Any]:
        info = self.emergency.trigger(reason)
        entry = self.audit.record("EMERGENCY_STOP", "kill_switch", "ENGAGED", "CRITICAL", info)
        self.events.emit(EventType.AUDIT_ENTRY, entry=entry)
        self.events.emit(EventType.EMERGENCY_STOP, **info)
        return info

    def reset_emergency_stop(self) -> None:
        self.emergency.reset()
        entry = self.audit.record("EMERGENCY_STOP", "kill_switch", "RESET", "SENSITIVE", {})
        self.events.emit(EventType.AUDIT_ENTRY, entry=entry)
        self.events.emit(EventType.EMERGENCY_RESET)

    def _on_emergency_triggered(self, _reason: str) -> None:
        self.speech.stop()
        self.voice.stop()
        self.utilities.cancel_all_timers()
        self.browser.close()

    def _handle_meta_command(self, cmd: str) -> str:
        if cmd == "emergency_stop":
            self.trigger_emergency_stop("Voice/text emergency stop command")
            return "Emergency Stop engaged. All tasks, timers, processes, and voice input have been halted."
        if cmd == "cancel_task":
            n = self.cancel_current_task()
            return f"Cancelled {n} active task(s) and stopped speech output."
        if cmd == "mute_tts":
            self.toggle_mute(True)
            return "Voice output muted."
        if cmd == "unmute_tts":
            self.toggle_mute(False)
            return "Voice output unmuted."
        return f"Handled control command: {cmd}."

    # ---------------- Personal Utility Notifications ----------------

    def _on_reminder_due(self, reminder: Dict[str, Any]) -> None:
        msg = f"Reminder: {reminder.get('message', '')}"
        self.events.emit(EventType.REMINDER_DUE, reminder=reminder, message=msg)
        if not self.speech.is_muted and not self.emergency.is_engaged:
            self.speech.speak(msg, interrupt_current=False)

    def _on_timer_finished(self, timer: Dict[str, Any]) -> None:
        msg = f"Your timer '{timer.get('label', 'Timer')}' is complete."
        self.events.emit(EventType.TIMER_FINISHED, timer=timer, message=msg)
        if not self.speech.is_muted and not self.emergency.is_engaged:
            self.speech.speak(msg, interrupt_current=False)

    # ---------------- Diagnostics ----------------

    def run_environment_doctor(self) -> Dict[str, Any]:
        return inspect_environment(self.config, model_manager=self.models, monitor=self.monitor)

    def save_settings(self) -> Path:
        return save_config(self.config)
