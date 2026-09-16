"""Direct-JSON persistence for aegis-platform user env variables.

Deliberately does NOT import tools/user_env_store.py — this module talks to
``$HERMES_HOME/users.env.json`` as a plain JSON channel (per design decision),
leaving the Hermes core untouched.

Key rules (mirroring tools/user_env_store.py semantics):
  - user_key format: ``aegis.{quote(user_id, safe=".-_~")}``
  - ``CURRENT_USER_NAME`` is a system-reserved key: never created/updated/
    deleted through this channel.
  - Only ``aegis.`` prefixed partitions are managed here.
  - Values are masked (first/last 4 chars kept) before leaving this store —
    plaintext never crosses the API boundary.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

MASK_KEEP = 4
RESERVED_KEYS = {"CURRENT_USER_NAME"}
AEGIS_PARTITION_PREFIX = "aegis."


class UserEnvAdminError(Exception):
    """User-facing error for userenv admin operations."""


def mask_value(value: str) -> str:
    """Keep first/last MASK_KEEP chars; short values mask entirely."""
    text = str(value or "")
    if len(text) <= MASK_KEEP * 2:
        return "*" * len(text)
    return f"{text[:MASK_KEEP]}{'*' * (len(text) - MASK_KEEP * 2)}{text[-MASK_KEEP:]}"


def make_aegis_user_key(user_id: Any) -> str:
    return f"{AEGIS_PARTITION_PREFIX}{quote(str(user_id or '').strip(), safe='.-_~')}"


class UserEnvAdminStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (get_hermes_home() / "users.env.json")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # -- raw JSON channel ----------------------------------------------------

    def _read_unlocked(self) -> dict[str, dict[str, str]]:
        if not self._path.exists():
            return {}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not read user env store %s: %s", self._path, exc)
            return {}
        if not isinstance(payload, dict):
            return {}
        result: dict[str, dict[str, str]] = {}
        for user_key, raw_env in payload.items():
            if not isinstance(user_key, str) or not isinstance(raw_env, dict):
                continue
            result[user_key] = {
                str(k): str(v)
                for k, v in raw_env.items()
                if isinstance(k, str) and v is not None
            }
        return result

    def _write_unlocked(self, payload: dict[str, dict[str, str]]) -> None:
        # backup then atomic write (temp + rename)
        try:
            if self._path.exists():
                backup = self._path.with_suffix(self._path.suffix + ".bak")
                backup.write_text(self._path.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception as exc:
            logger.warning("User env store backup failed: %s", exc)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self._path)

    # -- public API ------------------------------------------------------------

    def _visible_env(self, user_key: str) -> dict[str, str]:
        env = self._read_unlocked().get(user_key, {})
        return {k: v for k, v in env.items() if k not in RESERVED_KEYS}

    def list_for_user(self, user_id: Any) -> dict[str, str]:
        """Return {env_key: masked_value} for the user's own aegis partition."""
        user_key = make_aegis_user_key(user_id)
        with self._lock:
            return {k: mask_value(v) for k, v in self._visible_env(user_key).items()}

    def _validate_env_key(self, key: Any) -> str:
        env_key = str(key or "").strip()
        if not env_key:
            raise UserEnvAdminError("Environment variable name is required")
        if "=" in env_key or "\x00" in env_key:
            raise UserEnvAdminError("Environment variable name cannot contain '=' or NUL bytes")
        if env_key in RESERVED_KEYS:
            raise UserEnvAdminError(f"'{env_key}' is a system-reserved key and cannot be modified")
        return env_key

    def set_var(self, user_id: Any, user_name: Any, key: Any, value: Any) -> dict[str, str]:
        env_key = self._validate_env_key(key)
        user_key = make_aegis_user_key(user_id)
        with self._lock:
            payload = self._read_unlocked()
            env = payload.get(user_key, {})
            env[env_key] = str(value)
            env["CURRENT_USER_NAME"] = str(user_name or "").strip()
            payload[user_key] = env
            self._write_unlocked(payload)
            return {k: mask_value(v) for k, v in env.items() if k not in RESERVED_KEYS}

    def delete_var(self, user_id: Any, key: Any) -> bool:
        env_key = self._validate_env_key(key)
        user_key = make_aegis_user_key(user_id)
        with self._lock:
            payload = self._read_unlocked()
            env = payload.get(user_key)
            if not env or env_key not in env:
                return False
            del env[env_key]
            payload[user_key] = env
            self._write_unlocked(payload)
            return True
