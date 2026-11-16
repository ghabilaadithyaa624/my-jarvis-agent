"""Persistent Conversation History & Context Window Builder."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from jarvis.memory.database import LocalDatabase


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationStore:
    """Stores multi-session chat history locally and slices token-safe context windows."""

    def __init__(self, db: LocalDatabase) -> None:
        self.db = db
        self._current_session_id = self.create_session("New Session")

    @property
    def current_session_id(self) -> str:
        return self._current_session_id

    def create_session(self, title: str = "New Session") -> str:
        sid = uuid.uuid4().hex[:12]
        now = _utc_now()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (sid, title[:120], now, now),
            )
        self._current_session_id = sid
        return sid

    def switch_session(self, session_id: str) -> bool:
        rows = self.db.query("SELECT id FROM sessions WHERE id = ?", (session_id,))
        if not rows:
            return False
        self._current_session_id = session_id
        return True

    def list_sessions(self, limit: int = 30) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT id, title, created_at, updated_at FROM sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    def delete_session(self, session_id: str) -> bool:
        with self.db.transaction() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            deleted = cur.rowcount > 0
        if deleted and session_id == self._current_session_id:
            existing = self.list_sessions(limit=1)
            self._current_session_id = existing[0]["id"] if existing else self.create_session("New Session")
        return deleted

    def add_message(
        self,
        role: str,
        content: str,
        *,
        session_id: Optional[str] = None,
        model: str = "",
        verified: Optional[bool] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        sid = session_id or self._current_session_id
        now = _utc_now()
        verified_int = None if verified is None else (1 if verified else 0)
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO messages (session_id, role, content, model, verified, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (sid, role, content, model, verified_int, meta_json, now),
            )
            msg_id = int(cur.lastrowid)
            # Auto-title new sessions from the first user prompt
            if role == "user":
                conn.execute(
                    """
                    UPDATE sessions
                    SET title = CASE WHEN title = 'New Session' THEN ? ELSE title END,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (content.strip().replace("\n", " ")[:60] or "Session", now, sid),
                )
            else:
                conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, sid))
        return msg_id

    def get_messages(
        self,
        session_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        sid = session_id or self._current_session_id
        rows = self.db.query(
            """
            SELECT id, session_id, role, content, model, verified, metadata_json, created_at
            FROM (
                SELECT * FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?
            ) ORDER BY id ASC
            """,
            (sid, limit),
        )
        out: List[Dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            item["verified"] = None if item["verified"] is None else bool(item["verified"])
            try:
                item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            except json.JSONDecodeError:
                item["metadata"] = {}
            out.append(item)
        return out

    def build_llm_context(
        self,
        max_messages: int = 10,
        max_chars: int = 6000,
        session_id: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Return bounded recent user/assistant messages safe for a 4096-token window."""
        msgs = self.get_messages(session_id=session_id, limit=max_messages)
        selected: List[Dict[str, str]] = []
        total_chars = 0
        for m in reversed(msgs):
            if m["role"] not in {"user", "assistant"}:
                continue
            text = str(m["content"])
            if len(text) > 1500:
                text = text[:1500] + " ...[truncated]"
            if total_chars + len(text) > max_chars and selected:
                break
            selected.append({"role": m["role"], "content": text})
            total_chars += len(text)
        selected.reverse()
        return selected

    def search_messages(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        q = f"%{query.strip()}%"
        rows = self.db.query(
            """
            SELECT id, session_id, role, content, created_at
            FROM messages WHERE content LIKE ? ORDER BY id DESC LIMIT ?
            """,
            (q, limit),
        )
        return [dict(r) for r in rows]
