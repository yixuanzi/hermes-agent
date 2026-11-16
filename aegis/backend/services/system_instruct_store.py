"""SQLite persistence for administrator-managed Aegis system instructions."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS system_instructs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    describe TEXT NOT NULL DEFAULT '',
    instruct TEXT NOT NULL,
    create_time TEXT NOT NULL,
    update_time TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_system_instructs_updated
    ON system_instructs (update_time DESC, id ASC);
"""


class SystemInstructStore:
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
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(system_instructs)").fetchall()
            }
            if "describe" not in columns:
                conn.execute(
                    "ALTER TABLE system_instructs "
                    "ADD COLUMN describe TEXT NOT NULL DEFAULT ''"
                )
            conn.commit()

    def list(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, describe, instruct, create_time, update_time, status "
                "FROM system_instructs ORDER BY update_time DESC, id ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, instruct_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id, name, describe, instruct, create_time, update_time, status "
                "FROM system_instructs WHERE id = ?",
                (instruct_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def create(self, record: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO system_instructs "
                "(id, name, describe, instruct, create_time, update_time, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    record["id"],
                    record["name"],
                    record["describe"],
                    record["instruct"],
                    record["create_time"],
                    record["update_time"],
                    record["status"],
                ),
            )
            conn.commit()

    def update(self, instruct_id: str, record: dict[str, Any]) -> bool:
        with self._lock, self._connect() as conn:
            result = conn.execute(
                "UPDATE system_instructs "
                "SET name = ?, describe = ?, instruct = ?, update_time = ?, status = ? "
                "WHERE id = ?",
                (
                    record["name"],
                    record["describe"],
                    record["instruct"],
                    record["update_time"],
                    record["status"],
                    instruct_id,
                ),
            )
            conn.commit()
        return result.rowcount == 1

    def delete(self, instruct_id: str) -> bool:
        with self._lock, self._connect() as conn:
            result = conn.execute(
                "DELETE FROM system_instructs WHERE id = ?",
                (instruct_id,),
            )
            conn.commit()
        return result.rowcount == 1
