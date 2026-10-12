import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS intakes (
    session_id TEXT PRIMARY KEY,
    company TEXT NOT NULL DEFAULT '{}',
    project TEXT NOT NULL DEFAULT '{}',
    project_status TEXT NOT NULL DEFAULT 'draft'
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_session ON messages (session_id, id);
"""


class IntakeStore:
    def __init__(self, path: str) -> None:
        self.path = path
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def get_intake(self, session_id: str) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT company, project, project_status FROM intakes WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return {"company": {}, "project": {}, "project_status": "draft"}
        company, project, status = row
        return {
            "company": json.loads(company),
            "project": json.loads(project),
            "project_status": status,
        }

    def save_section(self, session_id: str, section: str, fields: dict) -> None:
        assert section in ("company", "project")
        merged = {**self.get_intake(session_id)[section], **fields}
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO intakes (session_id) VALUES (?)", (session_id,)
            )
            conn.execute(
                f"UPDATE intakes SET {section} = ? WHERE session_id = ?",
                (json.dumps(merged, ensure_ascii=False), session_id),
            )

    def set_project_status(self, session_id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO intakes (session_id) VALUES (?)", (session_id,)
            )
            conn.execute(
                "UPDATE intakes SET project_status = ? WHERE session_id = ?",
                (status, session_id),
            )

    def append_message(self, session_id: str, role: str, content: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                (session_id, role, content),
            )

    def history(self, session_id: str, limit: int = 40) -> list[tuple[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content FROM (
                    SELECT id, role, content FROM messages
                    WHERE session_id = ? ORDER BY id DESC LIMIT ?
                ) ORDER BY id ASC
                """,
                (session_id, limit),
            ).fetchall()
        return [(role, content) for role, content in rows]
