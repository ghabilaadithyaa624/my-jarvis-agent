"""Long-term persistent user knowledge, preferences & project alias store."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List

from jarvis.memory.database import LocalDatabase


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeStore:
    """Stores persistent facts (e.g., preferred IDE, Blender project path, user habits)."""

    def __init__(self, db: LocalDatabase) -> None:
        self.db = db

    def remember(
        self,
        key: str,
        value: str,
        category: str = "general",
        pinned: bool = False,
    ) -> Dict[str, Any]:
        k = key.strip()
        v = value.strip()
        cat = (category or "general").strip().lower()
        if not k or not v:
            raise ValueError("Both 'key' and 'value' are required to store a memory fact.")
        now = _utc_now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO facts (key, value, category, pinned, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    category = excluded.category,
                    pinned = excluded.pinned,
                    updated_at = excluded.updated_at
                """,
                (k, v, cat, 1 if pinned else 0, now, now),
            )
            if self.db.has_fts5:
                conn.execute("INSERT INTO facts_fts(facts_fts) VALUES('rebuild');")

        rows = self.db.query("SELECT * FROM facts WHERE key = ?", (k,))
        return dict(rows[0]) if rows else {"key": k, "value": v, "category": cat}

    def recall(self, query: str = "", limit: int = 10) -> List[Dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            rows = self.db.query(
                "SELECT * FROM facts ORDER BY pinned DESC, updated_at DESC LIMIT ?",
                (limit,),
            )
            return [dict(r) for r in rows]

        results: List[Dict[str, Any]] = []
        seen_ids: set[int] = set()

        if self.db.has_fts5:
            tokens = [t for t in re.findall(r"[\w\u0B80-\u0BFF]+", q) if len(t) >= 2]
            if tokens:
                fts_expr = " OR ".join(f'"{t}"*' for t in tokens[:8])
                try:
                    rows = self.db.query(
                        """
                        SELECT f.* FROM facts_fts
                        JOIN facts f ON f.id = facts_fts.rowid
                        WHERE facts_fts MATCH ?
                        ORDER BY f.pinned DESC, rank
                        LIMIT ?
                        """,
                        (fts_expr, limit),
                    )
                    for r in rows:
                        d = dict(r)
                        seen_ids.add(d["id"])
                        results.append(d)
                except Exception:
                    pass

        if len(results) < limit:
            like = f"%{q}%"
            rows = self.db.query(
                """
                SELECT * FROM facts
                WHERE key LIKE ? OR value LIKE ? OR category LIKE ?
                ORDER BY pinned DESC, updated_at DESC LIMIT ?
                """,
                (like, like, like, limit),
            )
            for r in rows:
                d = dict(r)
                if d["id"] not in seen_ids:
                    seen_ids.add(d["id"])
                    results.append(d)
                    if len(results) >= limit:
                        break
        return results

    def get_pinned_and_relevant(self, user_input: str, limit: int = 6) -> List[Dict[str, Any]]:
        pinned = self.db.query(
            "SELECT * FROM facts WHERE pinned = 1 ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )
        out = [dict(r) for r in pinned]
        seen = {item["id"] for item in out}
        if len(out) < limit:
            for item in self.recall(user_input, limit=limit):
                if item["id"] not in seen:
                    out.append(item)
                    seen.add(item["id"])
                    if len(out) >= limit:
                        break
        return out

    def list_all(self, limit: int = 200) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM facts ORDER BY pinned DESC, updated_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    def delete(self, fact_id: int) -> bool:
        with self.db.transaction() as conn:
            cur = conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
            deleted = cur.rowcount > 0
            if deleted and self.db.has_fts5:
                conn.execute("INSERT INTO facts_fts(facts_fts) VALUES('rebuild');")
        return deleted

    def clear_all(self) -> int:
        with self.db.transaction() as conn:
            cur = conn.execute("DELETE FROM facts")
            count = cur.rowcount
            if self.db.has_fts5:
                conn.execute("INSERT INTO facts_fts(facts_fts) VALUES('rebuild');")
        return count
