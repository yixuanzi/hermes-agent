"""Durable poll state: per-source watermarks, handled message ids, review drafts.

One SQLite database per Lark account in the plugin's data dir
(``<hermes home>/plugin-data/lark_user/<open_id>.db``), so a restart neither replays handled
messages nor loses pending drafts. Every change writes only its own row and commits at once:
a poll marks a few ids seen instead of rewriting the whole history. Message ids are the source of
truth for "already handled": the CLI renders ``create_time`` to the minute, so watermarks only
narrow the search window.

Drafts are few and read on every review click, so they are also cached in memory (write-through).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from hermes_constants import get_hermes_home
from plugins.plugin_storage import plugin_db

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS watermarks (source TEXT PRIMARY KEY, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS seen (message_id TEXT PRIMARY KEY, at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS seen_at ON seen (at);
CREATE TABLE IF NOT EXISTS drafts (number TEXT PRIMARY KEY, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def legacy_state_path(open_id: str) -> Path:
    """Where the JSON state file lived before the database; imported once, then renamed."""
    return get_hermes_home() / "lark_user" / f"{open_id}.json"


class CursorStore:
    def __init__(self, db: sqlite3.Connection, *, seen_ttl: float):
        self._db = db
        self._seen_ttl = seen_ttl
        with db:
            db.executescript(_SCHEMA)
        self._drafts = {number: json.loads(record) for number, record in db.execute("SELECT number, record FROM drafts")}

    @classmethod
    def open(cls, open_id: str, *, seen_ttl: float) -> CursorStore:
        store = cls(plugin_db("lark_user", f"{open_id}.db"), seen_ttl=seen_ttl)
        store.import_legacy(legacy_state_path(open_id))
        return store

    def close(self) -> None:
        self._db.close()

    # ── poll position ───────────────────────────────────────────────────────

    def watermark(self, source: str) -> Optional[float]:
        row = self._db.execute("SELECT at FROM watermarks WHERE source = ?", (source,)).fetchone()
        return row[0] if row else None

    def advance(self, source: str, at: float) -> None:
        """Move ``source``'s watermark forward to ``at``; never back."""
        with self._db:
            self._db.execute("INSERT INTO watermarks VALUES (?, ?) "
                             "ON CONFLICT (source) DO UPDATE SET at = max(at, excluded.at)", (source, at))

    def is_seen(self, message_id: str) -> bool:
        return self._db.execute("SELECT 1 FROM seen WHERE message_id = ?", (message_id,)).fetchone() is not None

    def mark_seen(self, message_id: str, *, now: Optional[float] = None) -> None:
        with self._db:
            self._db.execute("INSERT OR REPLACE INTO seen VALUES (?, ?)",
                             (message_id, now if now is not None else time.time()))

    def prune(self, now: Optional[float] = None) -> None:
        """Forget handled ids older than the TTL: no search window reaches back that far."""
        with self._db:
            self._db.execute("DELETE FROM seen WHERE at < ?", ((now or time.time()) - self._seen_ttl,))

    # ── review drafts ───────────────────────────────────────────────────────

    @property
    def drafts(self) -> dict[str, dict[str, Any]]:
        """Pending drafts by number (a snapshot: change one through ``put_draft``)."""
        return dict(self._drafts)

    def draft(self, number: str) -> Optional[dict[str, Any]]:
        return self._drafts.get(number)

    def put_draft(self, number: str, record: dict[str, Any]) -> None:
        with self._db:
            self._db.execute("INSERT OR REPLACE INTO drafts VALUES (?, ?)",
                             (number, json.dumps(record, ensure_ascii=False)))
        self._drafts[number] = record

    def pop_draft(self, number: str) -> dict[str, Any]:
        record = self._drafts[number]
        with self._db:
            self._db.execute("DELETE FROM drafts WHERE number = ?", (number,))
        del self._drafts[number]
        return record

    def new_draft_number(self) -> str:
        """The next draft number; never reused, so an old card or command cannot hit a newer draft."""
        with self._db:
            number = int(self._meta("next_draft") or 1)
            self._set_meta("next_draft", str(number + 1))
        return str(number)

    @property
    def review_chat(self) -> str:
        return self._meta("review_chat") or ""

    @review_chat.setter
    def review_chat(self, chat_id: str) -> None:
        with self._db:
            self._set_meta("review_chat", chat_id)

    def _meta(self, key: str) -> Optional[str]:
        row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _set_meta(self, key: str, value: str) -> None:
        self._db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))

    # ── migration ───────────────────────────────────────────────────────────

    def import_legacy(self, path: Path) -> None:
        """Take in a pre-database JSON state file once, then rename it ``*.json.migrated``."""
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            logger.warning("lark_user: unreadable legacy state file %s (%s); not imported", path.name, exc)
            return
        with self._db:
            for source, at in (data.get("watermarks") or {}).items():
                self._db.execute("INSERT INTO watermarks VALUES (?, ?) "
                                 "ON CONFLICT (source) DO UPDATE SET at = max(at, excluded.at)", (str(source), float(at)))
            self._db.executemany("INSERT OR IGNORE INTO seen VALUES (?, ?)",
                                 [(str(k), float(v)) for k, v in (data.get("seen") or {}).items()])
            drafts = {str(k): v for k, v in (data.get("drafts") or {}).items() if isinstance(v, dict)}
            self._db.executemany("INSERT OR IGNORE INTO drafts VALUES (?, ?)",
                                 [(k, json.dumps(v, ensure_ascii=False)) for k, v in drafts.items()])
            if data.get("review_chat") and not self._meta("review_chat"):
                self._set_meta("review_chat", str(data["review_chat"]))
            following = max([int(data.get("next_draft") or 1), int(self._meta("next_draft") or 1),
                             *(int(k) + 1 for k in drafts if k.isdigit())])
            self._set_meta("next_draft", str(following))
        for number, record in drafts.items():
            self._drafts.setdefault(number, record)
        path.rename(path.with_name(path.name + ".migrated"))
        logger.info("lark_user: imported %s into the state database", path.name)
