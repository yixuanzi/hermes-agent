"""SQLite persistence for user-scoped Aegis prompt templates."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS prompt_templates (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    tag TEXT NOT NULL,
    desc TEXT NOT NULL DEFAULT '',
    prompt TEXT NOT NULL,
    create_time TEXT NOT NULL,
    update_time TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prompt_templates_user_tag_updated
    ON prompt_templates (user_id, tag, update_time DESC);
"""


class PromptTemplateStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (get_hermes_home() / "aegis.db")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id, tag, desc, prompt, create_time, update_time "
                "FROM prompt_templates WHERE user_id = ? "
                "ORDER BY tag COLLATE NOCASE ASC, update_time DESC, id ASC",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_for_user(self, template_id: str, user_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id, tag, desc, prompt, create_time, update_time "
                "FROM prompt_templates WHERE id = ? AND user_id = ?",
                (template_id, user_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def create(self, record: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO prompt_templates "
                "(id, user_id, tag, desc, prompt, create_time, update_time) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    record["id"], record["user_id"], record["tag"], record["desc"],
                    record["prompt"], record["create_time"], record["update_time"],
                ),
            )
            conn.commit()

    def update_for_user(self, template_id: str, user_id: str, record: dict[str, Any]) -> bool:
        with self._lock, self._connect() as conn:
            result = conn.execute(
                "UPDATE prompt_templates SET tag = ?, desc = ?, prompt = ?, update_time = ? "
                "WHERE id = ? AND user_id = ?",
                (record["tag"], record["desc"], record["prompt"], record["update_time"], template_id, user_id),
            )
            conn.commit()
        return result.rowcount == 1

    def delete_for_user(self, template_id: str, user_id: str) -> bool:
        with self._lock, self._connect() as conn:
            result = conn.execute(
                "DELETE FROM prompt_templates WHERE id = ? AND user_id = ?",
                (template_id, user_id),
            )
            conn.commit()
        return result.rowcount == 1

