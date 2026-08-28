from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    uid TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    passwd TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    oidc_subject TEXT UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('enabled', 'disabled')),
    create_time TEXT NOT NULL,
    last_login TEXT,
    display_name TEXT
);

CREATE TABLE IF NOT EXISTS oidc_login_transactions (
    state TEXT PRIMARY KEY,
    nonce TEXT NOT NULL,
    code_verifier TEXT NOT NULL,
    client_id TEXT NOT NULL,
    organization_id TEXT,
    flow TEXT NOT NULL CHECK(flow IN ('direct', 'sso')),
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_oidc_login_transactions_expires
    ON oidc_login_transactions (expires_at);

CREATE TABLE IF NOT EXISTS sso_login_tickets (
    ticket_digest TEXT PRIMARY KEY,
    user_uid TEXT NOT NULL REFERENCES users(uid),
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sso_login_tickets_expires
    ON sso_login_tickets (expires_at);
"""

_USER_COLUMNS = "uid, username, passwd, email, oidc_subject, status, create_time, last_login, display_name"


class AisocUserStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (get_hermes_home() / "aisoc.db")
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
            try:
                conn.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
            except sqlite3.OperationalError:
                pass
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()
            }
            if "oidc_subject" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN oidc_subject TEXT")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_oidc_subject "
                "ON users (oidc_subject) WHERE oidc_subject IS NOT NULL"
            )
            conn.commit()

    def list_users(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_USER_COLUMNS} FROM users ORDER BY username ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_user_by_uid(self, uid: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                f"SELECT {_USER_COLUMNS} FROM users WHERE uid = ?",
                (uid,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                f"SELECT {_USER_COLUMNS} FROM users WHERE username = ?",
                (username,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_user_by_oidc_subject(self, subject: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                f"SELECT {_USER_COLUMNS} FROM users WHERE oidc_subject = ?",
                (subject,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                f"SELECT {_USER_COLUMNS} FROM users WHERE lower(email) = lower(?)",
                (email,),
            ).fetchone()
        return dict(row) if row is not None else None

    def create_user(self, record: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO users (uid, username, passwd, email, oidc_subject, status, create_time, last_login, display_name) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record["uid"],
                    record["username"],
                    record["passwd"],
                    record["email"],
                    record.get("oidc_subject"),
                    record["status"],
                    record["create_time"],
                    record.get("last_login"),
                    record.get("display_name") or record["username"],
                ),
            )
            conn.commit()

    def update_oidc_identity(self, uid: str, subject: str, email: str, last_login: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE users SET oidc_subject = ?, email = ?, last_login = ? WHERE uid = ?",
                (subject, email, last_login, uid),
            )
            conn.commit()

    def create_oidc_login_transaction(self, record: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO oidc_login_transactions "
                "(state, nonce, code_verifier, client_id, organization_id, flow, expires_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record["state"],
                    record["nonce"],
                    record["code_verifier"],
                    record["client_id"],
                    record.get("organization_id"),
                    record["flow"],
                    record["expires_at"],
                    record["created_at"],
                ),
            )
            conn.commit()

    def get_oidc_login_transaction(self, state: str, now: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT state, nonce, code_verifier, client_id, organization_id, flow, "
                "expires_at, consumed_at, created_at "
                "FROM oidc_login_transactions "
                "WHERE state = ? AND consumed_at IS NULL AND expires_at > ?",
                (state, now),
            ).fetchone()
        return dict(row) if row is not None else None

    def consume_oidc_login_transaction(self, state: str, consumed_at: str) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "UPDATE oidc_login_transactions SET consumed_at = ? "
                "WHERE state = ? AND consumed_at IS NULL",
                (consumed_at, state),
            )
            conn.commit()
        return cursor.rowcount == 1

    def create_sso_login_ticket(self, record: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO sso_login_tickets "
                "(ticket_digest, user_uid, expires_at, created_at) VALUES (?, ?, ?, ?)",
                (
                    record["ticket_digest"],
                    record["user_uid"],
                    record["expires_at"],
                    record["created_at"],
                ),
            )
            conn.commit()

    def claim_sso_login_ticket(self, ticket_digest: str, consumed_at: str, now: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "UPDATE sso_login_tickets SET consumed_at = ? "
                "WHERE ticket_digest = ? AND consumed_at IS NULL AND expires_at > ?",
                (consumed_at, ticket_digest, now),
            )
            if cursor.rowcount != 1:
                conn.commit()
                return None
            row = conn.execute(
                "SELECT user_uid FROM sso_login_tickets WHERE ticket_digest = ?",
                (ticket_digest,),
            ).fetchone()
            conn.commit()
        return dict(row) if row is not None else None

    def update_password(self, uid: str, passwd: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE users SET passwd = ? WHERE uid = ?", (passwd, uid))
            conn.commit()

    def update_status(self, uid: str, status: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE users SET status = ? WHERE uid = ?", (status, uid))
            conn.commit()

    def update_last_login(self, uid: str, last_login: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE users SET last_login = ? WHERE uid = ?", (last_login, uid))
            conn.commit()

    def update_display_name(self, uid: str, display_name: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE users SET display_name = ? WHERE uid = ?", (display_name, uid)
            )
            conn.commit()

    def delete_user(self, uid: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM users WHERE uid = ?", (uid,))
            conn.commit()


_STORE: AisocUserStore | None = None
_STORE_LOCK = threading.Lock()


def get_aisoc_user_store() -> AisocUserStore:
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = AisocUserStore()
    return _STORE
