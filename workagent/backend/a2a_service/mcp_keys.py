"""Personal MCP keys for the WORKAGENT A2A module.

An administrator issues one key per person from the ``/man`` console.  Each key
carries a free-form identity object; a task started over ``/mcp`` with that key
has the identity prepended to its message as the ``<source>{...}</source>``
envelope the A2A executor already parses (platform / uid / uname / channel ...),
so the agent knows who is asking without trusting the caller to say so.

Only a SHA-256 digest of each key is stored: the plaintext is returned once, at
creation or regeneration, and cannot be recovered afterwards.  The store is a
0600 JSON file in the active profile's Hermes home.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import logging
from pathlib import Path
import secrets
import threading
import time
from typing import Any

from hermes_constants import get_hermes_home
from utils import atomic_json_write, read_json_or_empty


logger = logging.getLogger(__name__)

KEY_PREFIX = "wak_"
_MAX_NAME_CHARS = 100
_MAX_SOURCE_CHARS = 4096
# last_used_at is advisory; persisting it on every request would rewrite the
# file per tool call.
_LAST_USED_PERSIST_INTERVAL_SECS = 60.0
_SOURCE_CLOSE_TAG = "</source>"


class McpKeyError(ValueError):
    """A key request the store refuses; ``status_code`` maps it to HTTP."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(slots=True)
class McpKeyRecord:
    id: str
    name: str
    source: dict[str, Any]
    key_hash: str
    key_hint: str
    enabled: bool
    created_at: float
    updated_at: float
    last_used_at: float | None = None

    def public(self) -> dict[str, Any]:
        """The record as the management API shows it (never the digest)."""
        data = asdict(self)
        data.pop("key_hash")
        return data

    @property
    def owner(self) -> str:
        """A2A task-store owner scope for tasks this key starts."""
        return f"mcp-key:{self.id}"

    @property
    def session_prefix(self) -> str:
        """Every session this key opens starts with this; it may only continue those."""
        return f"mk-{self.id}-"

    def source_envelope(self) -> str:
        return f"<source>{encode_source(self.source)}</source>"


def default_store_path() -> Path:
    return get_hermes_home() / "workagent" / "mcp_keys.json"


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _new_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def _hint(key: str) -> str:
    return f"{key[: len(KEY_PREFIX) + 4]}…{key[-4:]}"


def encode_source(source: dict[str, Any]) -> str:
    return json.dumps(source, ensure_ascii=False, separators=(",", ":"))


def validate_name(name: object) -> str:
    text = str(name or "").strip()
    if not text:
        raise McpKeyError(422, "name is required")
    if len(text) > _MAX_NAME_CHARS:
        raise McpKeyError(422, f"name must be at most {_MAX_NAME_CHARS} characters")
    return text


def validate_source(source: object) -> dict[str, Any]:
    """Accept a JSON object, or a string holding one (with or without the tags)."""
    if isinstance(source, str):
        text = source.strip()
        if text.startswith("<source>") and text.endswith(_SOURCE_CLOSE_TAG):
            text = text[len("<source>") : -len(_SOURCE_CLOSE_TAG)].strip()
        try:
            source = json.loads(text)
        except ValueError as exc:
            raise McpKeyError(422, f"source is not valid JSON: {exc}") from exc
    if not isinstance(source, dict) or not source:
        raise McpKeyError(422, "source must be a non-empty JSON object")
    encoded = encode_source(source)
    if len(encoded) > _MAX_SOURCE_CHARS:
        raise McpKeyError(422, f"source must be at most {_MAX_SOURCE_CHARS} characters")
    if _SOURCE_CLOSE_TAG in encoded:
        # The executor ends the envelope at the first closing tag.
        raise McpKeyError(422, "source must not contain </source>")
    return source


class McpKeyStore:
    """Thread-safe, file-backed registry of personal MCP keys."""

    def __init__(self, path: Path | None = None, *, time_fn=time.time):
        self._path = path or default_store_path()
        self._time_fn = time_fn
        self._lock = threading.Lock()
        self._records: dict[str, McpKeyRecord] = {}
        self._by_hash: dict[str, str] = {}
        self._load()

    # ── persistence ─────────────────────────────────────────────────────
    def _load(self) -> None:
        for raw in read_json_or_empty(self._path).get("keys") or []:
            try:
                record = McpKeyRecord(**raw)
            except TypeError:
                logger.warning("Skipping malformed MCP key record in %s", self._path)
                continue
            self._records[record.id] = record
            self._by_hash[record.key_hash] = record.id

    def _save_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_write(
            self._path,
            {"version": 1, "keys": [asdict(record) for record in self._records.values()]},
            mode=0o600,
        )

    # ── queries ─────────────────────────────────────────────────────────
    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            records = sorted(self._records.values(), key=lambda record: record.created_at)
            return [record.public() for record in records]

    def get(self, key_id: str) -> dict[str, Any]:
        with self._lock:
            return self._require_locked(key_id).public()

    def authenticate(self, key: str | None) -> McpKeyRecord | None:
        """The enabled record ``key`` belongs to, or ``None``."""
        if not key or not key.startswith(KEY_PREFIX):
            return None
        digest = _digest(key)
        with self._lock:
            key_id = self._by_hash.get(digest)
            record = self._records.get(key_id) if key_id else None
            if record is None or not secrets.compare_digest(record.key_hash, digest):
                return None
            if not record.enabled:
                return None
            now = self._time_fn()
            persist = (
                record.last_used_at is None
                or now - record.last_used_at >= _LAST_USED_PERSIST_INTERVAL_SECS
            )
            record.last_used_at = now
            if persist:
                try:
                    self._save_locked()
                except OSError:
                    logger.warning("Could not persist MCP key last-use time", exc_info=True)
            return McpKeyRecord(**asdict(record))

    # ── mutations ───────────────────────────────────────────────────────
    def create(self, *, name: object, source: object) -> tuple[dict[str, Any], str]:
        """Create a key; returns ``(record, plaintext_key)``."""
        clean_name = validate_name(name)
        clean_source = validate_source(source)
        key = _new_key()
        now = self._time_fn()
        record = McpKeyRecord(
            id=secrets.token_hex(6),
            name=clean_name,
            source=clean_source,
            key_hash=_digest(key),
            key_hint=_hint(key),
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._records[record.id] = record
            self._by_hash[record.key_hash] = record.id
            self._save_locked()
            return record.public(), key

    def update(self, key_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        unknown = set(changes) - {"name", "source", "enabled"}
        if unknown:
            raise McpKeyError(422, f"unknown fields: {', '.join(sorted(unknown))}")
        updates: dict[str, Any] = {}
        if "name" in changes:
            updates["name"] = validate_name(changes["name"])
        if "source" in changes:
            updates["source"] = validate_source(changes["source"])
        if "enabled" in changes:
            if not isinstance(changes["enabled"], bool):
                raise McpKeyError(422, "enabled must be true or false")
            updates["enabled"] = changes["enabled"]
        with self._lock:
            record = self._require_locked(key_id)
            for field_name, value in updates.items():
                setattr(record, field_name, value)
            record.updated_at = self._time_fn()
            self._save_locked()
            return record.public()

    def regenerate(self, key_id: str) -> tuple[dict[str, Any], str]:
        """Replace a key's secret; the old one stops working at once."""
        key = _new_key()
        with self._lock:
            record = self._require_locked(key_id)
            self._by_hash.pop(record.key_hash, None)
            record.key_hash = _digest(key)
            record.key_hint = _hint(key)
            record.updated_at = self._time_fn()
            self._by_hash[record.key_hash] = record.id
            self._save_locked()
            return record.public(), key

    def delete(self, key_id: str) -> None:
        with self._lock:
            record = self._require_locked(key_id)
            del self._records[record.id]
            self._by_hash.pop(record.key_hash, None)
            self._save_locked()

    def _require_locked(self, key_id: str) -> McpKeyRecord:
        record = self._records.get(str(key_id or ""))
        if record is None:
            raise McpKeyError(404, "key not found")
        return record
