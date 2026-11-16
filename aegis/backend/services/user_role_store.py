"""SQLite persistence for administrator-managed user roles."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS user_roles (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    uid TEXT NOT NULL,
    uname TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'operator', 'admin')),
    update_time TEXT NOT NULL,
    UNIQUE(platform, uid)
);

CREATE INDEX IF NOT EXISTS idx_user_roles_updated
    ON user_roles (update_time DESC, id ASC);
"""


class UserRoleStore:
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

    def list(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id, platform, uid, uname, role, update_time "
                "FROM user_roles ORDER BY update_time DESC, id ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, role_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id, platform, uid, uname, role, update_time "
                "FROM user_roles WHERE id = ?",
                (role_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def create(self, record: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO user_roles "
                "(id, platform, uid, uname, role, update_time) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record["id"],
                    record["platform"],
                    record["uid"],
                    record["uname"],
                    record["role"],
                    record["update_time"],
                ),
            )
            conn.commit()

    def update(self, role_id: str, record: dict[str, Any]) -> bool:
        with self._lock, self._connect() as conn:
            result = conn.execute(
                "UPDATE user_roles SET platform = ?, uid = ?, uname = ?, "
                "role = ?, update_time = ? WHERE id = ?",
                (
                    record["platform"],
                    record["uid"],
                    record["uname"],
                    record["role"],
                    record["update_time"],
                    role_id,
                ),
            )
            conn.commit()
        return result.rowcount == 1

    def delete(self, role_id: str) -> bool:
        with self._lock, self._connect() as conn:
            result = conn.execute("DELETE FROM user_roles WHERE id = ?", (role_id,))
            conn.commit()
        return result.rowcount == 1
