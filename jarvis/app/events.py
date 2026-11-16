"""Thread-safe typed publish/subscribe EventBus decoupling backend & PySide6 GUI."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List


class EventType(str, Enum):
    OLLAMA_STATUS = "ollama_status"
    MODELS_UPDATED = "models_updated"
    MODEL_CHANGED = "model_changed"
    VOICE_STATE = "voice_state"
    AUDIO_LEVEL = "audio_level"
    TRANSCRIPT_READY = "transcript_ready"
    TTS_STATE = "tts_state"
    MESSAGE_ADDED = "message_added"
    STREAM_START = "stream_start"
    STREAM_TOKEN = "stream_token"
    STREAM_END = "stream_end"
    TASK_STAGE = "task_stage"
    PLAN_CREATED = "plan_created"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    PERMISSION_REQUEST = "permission_request"
    AUDIT_ENTRY = "audit_entry"
    TELEMETRY_UPDATE = "telemetry_update"
    REMINDER_DUE = "reminder_due"
    TIMER_FINISHED = "timer_finished"
    EMERGENCY_STOP = "emergency_stop"
    EMERGENCY_RESET = "emergency_reset"
    ERROR = "error"


@dataclass(frozen=True)
class AppEvent:
    type: EventType
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


Subscriber = Callable[[AppEvent], None]


class EventBus:
    """Thread-safe synchronous dispatcher; GUI subscribers bridge via Qt Signals."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscribers: Dict[EventType, List[Subscriber]] = {}
        self._wildcard: List[Subscriber] = []

    def subscribe(self, event_type: EventType, callback: Subscriber) -> Callable[[], None]:
        with self._lock:
            self._subscribers.setdefault(event_type, []).append(callback)

        def unsubscribe() -> None:
            with self._lock:
                lst = self._subscribers.get(event_type, [])
                if callback in lst:
                    lst.remove(callback)

        return unsubscribe

    def subscribe_all(self, callback: Subscriber) -> Callable[[], None]:
        with self._lock:
            self._wildcard.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._wildcard:
                    self._wildcard.remove(callback)

        return unsubscribe

    def emit(self, event_type: EventType, **payload: Any) -> AppEvent:
        event = AppEvent(type=event_type, payload=payload)
        with self._lock:
            targets = list(self._subscribers.get(event_type, ())) + list(self._wildcard)
        for cb in targets:
            try:
                cb(event)
            except Exception:
                # Never allow a faulty UI subscriber to crash an agent worker
                pass
        return event
