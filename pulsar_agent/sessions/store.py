"""SQLite+FTS5 session persistence. All content is redacted before insert.

Thread-safety: the TUI runs agent turns in a worker thread while the store
is created on the main thread, so the connection is opened with
check_same_thread=False and every access is serialized behind one lock.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pulsar_agent.security.redaction import Redactor

DB_FILENAME = "state.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    title TEXT DEFAULT '',
    workspace TEXT DEFAULT '',
    model_id TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_name TEXT DEFAULT ''
);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    session_id UNINDEXED,
    message_id UNINDEXED
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _fts_query(raw: str) -> str:
    # OR-join quoted tokens: forgiving recall, and user input can never break
    # out into FTS5 query syntax.
    tokens = re.findall(r"\w+", raw)
    return " OR ".join(f'"{token}"' for token in tokens)


class SessionStore:
    def __init__(self, db_path: Path, redactor: Redactor | None = None):
        self.db_path = db_path
        self.redactor = redactor or Redactor(enabled=False)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_session(
        self, workspace: str = "", model_id: str = "", title: str = ""
    ) -> str:
        session_id = uuid.uuid4().hex[:12]
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (id, created_at, updated_at, title, workspace, model_id)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, now, now, title, workspace, model_id),
            )
            self._conn.commit()
        return session_id

    def append_message(
        self, session_id: str, role: str, content: str, tool_name: str = ""
    ) -> int:
        safe_content = self.redactor.redact(content or "")
        now = _now()
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO messages (session_id, ts, role, content, tool_name)"
                " VALUES (?, ?, ?, ?, ?)",
                (session_id, now, role, safe_content, tool_name),
            )
            message_id = cursor.lastrowid
            self._conn.execute(
                "INSERT INTO messages_fts (content, session_id, message_id) VALUES (?, ?, ?)",
                (safe_content, session_id, message_id),
            )
            self._conn.execute(
                "UPDATE sessions SET updated_at = ?, title = CASE WHEN title = '' AND ? = 'user'"
                " THEN substr(?, 1, 60) ELSE title END WHERE id = ?",
                (now, role, safe_content, session_id),
            )
            self._conn.commit()
        return int(message_id or 0)

    def list_sessions(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, created_at, updated_at, title, workspace, model_id,"
                " (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count"
                " FROM sessions s ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        keys = ("id", "created_at", "updated_at", "title", "workspace", "model_id", "message_count")
        return [dict(zip(keys, row, strict=False)) for row in rows]

    def get_messages(self, session_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content, tool_name, ts FROM messages"
                " WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [
            {"role": role, "content": content, "tool_name": tool_name, "ts": ts}
            for role, content, tool_name, ts in rows
        ]

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._conn.execute(
                "DELETE FROM messages_fts WHERE session_id = ?", (session_id,)
            )
            self._conn.commit()
        return cursor.rowcount > 0

    def search(self, query: str, limit: int = 10) -> list[dict]:
        fts = _fts_query(query)
        if not fts:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id, message_id,"
                " snippet(messages_fts, 0, '[', ']', '…', 12) AS snip"
                " FROM messages_fts WHERE messages_fts MATCH ?"
                " ORDER BY rank LIMIT ?",
                (fts, limit),
            ).fetchall()
        results = []
        for session_id, message_id, snip in rows:
            with self._lock:
                meta = self._conn.execute(
                    "SELECT title, updated_at FROM sessions WHERE id = ?", (session_id,)
                ).fetchone()
            results.append(
                {
                    "session_id": session_id,
                    "message_id": message_id,
                    "snippet": snip,
                    "title": meta[0] if meta else "",
                    "updated_at": meta[1] if meta else "",
                }
            )
        return results
