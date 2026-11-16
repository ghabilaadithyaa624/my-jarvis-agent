"""Personal Utilities Store & Live Scheduler: Reminders, Notes, To-Dos, Timers & Task History."""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from jarvis.memory.database import LocalDatabase


def _local_now() -> datetime:
    return datetime.now().astimezone()


def parse_remind_time(when_str: str) -> datetime:
    """Parse flexible natural/ISO time strings ('in 10 minutes', '18:30', '2026-11-16 19:00')."""
    now = _local_now()
    s = (when_str or "").strip().lower()
    if not s:
        return now + timedelta(minutes=5)

    # Relative expressions: "in 15 minutes", "30 seconds", "2 hours"
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)\b",
        s,
    )
    if m:
        val = float(m.group(1))
        unit = m.group(2)
        if unit.startswith("s"):
            return now + timedelta(seconds=val)
        if unit.startswith("m"):
            return now + timedelta(minutes=val)
        if unit.startswith("h"):
            return now + timedelta(hours=val)
        if unit.startswith("d"):
            return now + timedelta(days=val)

    # HH:MM [am/pm]
    tm = re.match(r"^(\d{1,2}):(\d{2})\s*(am|pm)?$", s)
    if tm:
        hour = int(tm.group(1))
        minute = int(tm.group(2))
        ampm = tm.group(3)
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    # ISO 8601 fallback
    try:
        dt = datetime.fromisoformat(when_str.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=now.tzinfo)
        return dt
    except ValueError:
        raise ValueError(
            f"Could not parse reminder time '{when_str}'. Use formats like 'in 15 minutes', '18:30', or '2026-11-16 19:00'."
        )


class UtilitiesStore:
    """Persists personal notes, todos, reminders, timers, and agent task history."""

    def __init__(
        self,
        db: LocalDatabase,
        on_reminder: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_timer: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self.db = db
        self._on_reminder = on_reminder
        self._on_timer = on_timer
        self._timers_lock = threading.Lock()
        self._timers: Dict[str, Dict[str, Any]] = {}
        self._stop_scheduler = threading.Event()
        self._scheduler_thread: Optional[threading.Thread] = None

    # ---------------- Background Scheduler ----------------

    def start_scheduler(self) -> None:
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            return
        self._stop_scheduler.clear()
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop,
            name="jarvis-utilities-scheduler",
            daemon=True,
        )
        self._scheduler_thread.start()

    def stop_scheduler(self) -> None:
        self._stop_scheduler.set()
        self.cancel_all_timers()

    def _scheduler_loop(self) -> None:
        while not self._stop_scheduler.wait(timeout=1.0):
            try:
                self.check_due_reminders()
                self._check_timers()
            except Exception:
                pass

    # ---------------- Notes ----------------

    def save_note(self, title: str, content: str, tags: str = "") -> Dict[str, Any]:
        now = _local_now().isoformat()
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO notes (title, content, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (title.strip(), content.strip(), tags.strip(), now, now),
            )
            nid = int(cur.lastrowid)
        rows = self.db.query("SELECT * FROM notes WHERE id = ?", (nid,))
        return dict(rows[0])

    def search_notes(self, query: str = "", limit: int = 20) -> List[Dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            rows = self.db.query("SELECT * FROM notes ORDER BY updated_at DESC LIMIT ?", (limit,))
        else:
            like = f"%{q}%"
            rows = self.db.query(
                "SELECT * FROM notes WHERE title LIKE ? OR content LIKE ? OR tags LIKE ? ORDER BY updated_at DESC LIMIT ?",
                (like, like, like, limit),
            )
        return [dict(r) for r in rows]

    def delete_note(self, note_id: int) -> bool:
        with self.db.transaction() as conn:
            cur = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
            return cur.rowcount > 0

    # ---------------- To-Do List ----------------

    def add_todo(
        self,
        title: str,
        priority: str = "medium",
        due_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        prio = priority.lower() if priority.lower() in {"low", "medium", "high"} else "medium"
        now = _local_now().isoformat()
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO todos (title, priority, status, due_at, created_at) VALUES (?, ?, 'pending', ?, ?)",
                (title.strip(), prio, due_at, now),
            )
            tid = int(cur.lastrowid)
        rows = self.db.query("SELECT * FROM todos WHERE id = ?", (tid,))
        return dict(rows[0])

    def list_todos(self, status: str = "all", limit: int = 50) -> List[Dict[str, Any]]:
        st = (status or "all").lower()
        if st in {"pending", "completed", "cancelled"}:
            rows = self.db.query(
                "SELECT * FROM todos WHERE status = ? ORDER BY id DESC LIMIT ?",
                (st, limit),
            )
        else:
            rows = self.db.query("SELECT * FROM todos ORDER BY status DESC, id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def update_todo(self, todo_id: int, status: str = "completed") -> Optional[Dict[str, Any]]:
        st = status.lower()
        if st not in {"pending", "completed", "cancelled"}:
            raise ValueError("status must be 'pending', 'completed', or 'cancelled'.")
        completed_at = _local_now().isoformat() if st == "completed" else None
        with self.db.transaction() as conn:
            cur = conn.execute(
                "UPDATE todos SET status = ?, completed_at = ? WHERE id = ?",
                (st, completed_at, todo_id),
            )
            if cur.rowcount == 0:
                return None
        rows = self.db.query("SELECT * FROM todos WHERE id = ?", (todo_id,))
        return dict(rows[0]) if rows else None

    # ---------------- Reminders ----------------

    def create_reminder(self, message: str, when: str) -> Dict[str, Any]:
        due_dt = parse_remind_time(when)
        now = _local_now().isoformat()
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO reminders (message, remind_at, triggered, created_at) VALUES (?, ?, 0, ?)",
                (message.strip(), due_dt.isoformat(), now),
            )
            rid = int(cur.lastrowid)
        rows = self.db.query("SELECT * FROM reminders WHERE id = ?", (rid,))
        return dict(rows[0])

    def list_reminders(self, include_triggered: bool = False, limit: int = 30) -> List[Dict[str, Any]]:
        if include_triggered:
            rows = self.db.query("SELECT * FROM reminders ORDER BY remind_at DESC LIMIT ?", (limit,))
        else:
            rows = self.db.query(
                "SELECT * FROM reminders WHERE triggered = 0 ORDER BY remind_at ASC LIMIT ?",
                (limit,),
            )
        return [dict(r) for r in rows]

    def complete_reminder(self, reminder_id: int) -> bool:
        with self.db.transaction() as conn:
            cur = conn.execute("UPDATE reminders SET triggered = 1 WHERE id = ?", (reminder_id,))
            return cur.rowcount > 0

    def check_due_reminders(self) -> List[Dict[str, Any]]:
        now = _local_now()
        pending = self.list_reminders(include_triggered=False, limit=50)
        fired: List[Dict[str, Any]] = []
        for rem in pending:
            try:
                due = datetime.fromisoformat(rem["remind_at"])
                if due.tzinfo is None:
                    due = due.replace(tzinfo=now.tzinfo)
                if due <= now:
                    self.complete_reminder(rem["id"])
                    rem["triggered"] = 1
                    fired.append(rem)
                    if self._on_reminder:
                        self._on_reminder(rem)
            except Exception:
                continue
        return fired

    # ---------------- Live Timers ----------------

    def start_timer(self, duration_seconds: float, label: str = "Timer") -> Dict[str, Any]:
        secs = max(1.0, min(float(duration_seconds), 86400.0))
        timer_id = uuid.uuid4().hex[:8]
        now_mono = time.monotonic()
        ends_wall = (_local_now() + timedelta(seconds=secs)).isoformat()
        entry = {
            "id": timer_id,
            "label": label.strip() or "Timer",
            "duration_seconds": round(secs, 1),
            "end_monotonic": now_mono + secs,
            "ends_at": ends_wall,
            "status": "running",
        }
        with self._timers_lock:
            self._timers[timer_id] = entry
        return self._public_timer(entry)

    def _public_timer(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        remaining = max(0.0, round(entry["end_monotonic"] - time.monotonic(), 1))
        return {
            "id": entry["id"],
            "label": entry["label"],
            "duration_seconds": entry["duration_seconds"],
            "remaining_seconds": remaining if entry["status"] == "running" else 0.0,
            "ends_at": entry["ends_at"],
            "status": entry["status"],
        }

    def list_timers(self) -> List[Dict[str, Any]]:
        with self._timers_lock:
            return [
                self._public_timer(t)
                for t in self._timers.values()
                if t["status"] == "running"
            ]

    def cancel_timer(self, timer_id_or_label: str) -> bool:
        target = timer_id_or_label.strip().lower()
        with self._timers_lock:
            for tid, entry in self._timers.items():
                if entry["status"] == "running" and (
                    tid == target or entry["label"].lower() == target
                ):
                    entry["status"] = "cancelled"
                    return True
        return False

    def cancel_all_timers(self) -> int:
        cancelled = 0
        with self._timers_lock:
            for entry in self._timers.values():
                if entry["status"] == "running":
                    entry["status"] = "cancelled"
                    cancelled += 1
        return cancelled

    def _check_timers(self) -> List[Dict[str, Any]]:
        now_mono = time.monotonic()
        done: List[Dict[str, Any]] = []
        with self._timers_lock:
            for entry in self._timers.values():
                if entry["status"] == "running" and now_mono >= entry["end_monotonic"]:
                    entry["status"] = "finished"
                    done.append(self._public_timer(entry))
        for item in done:
            if self._on_timer:
                try:
                    self._on_timer(item)
                except Exception:
                    pass
        return done

    # ---------------- Task Execution History ----------------

    def log_task(
        self,
        *,
        session_id: str,
        user_input: str,
        intent: str,
        status: str,
        verified: bool,
        steps_count: int,
        duration_ms: float,
        summary: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> int:
        now = _local_now().isoformat()
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO task_history
                    (session_id, user_input, intent, status, verified, steps_count,
                     duration_ms, summary, details_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    user_input[:1000],
                    intent,
                    status,
                    1 if verified else 0,
                    steps_count,
                    round(duration_ms, 1),
                    summary[:1000],
                    json.dumps(details or {}, ensure_ascii=False),
                    now,
                ),
            )
            return int(cur.lastrowid)

    def get_task_history(self, limit: int = 30) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM task_history ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["verified"] = bool(d["verified"])
            try:
                d["details"] = json.loads(d.pop("details_json") or "{}")
            except json.JSONDecodeError:
                d["details"] = {}
            out.append(d)
        return out
