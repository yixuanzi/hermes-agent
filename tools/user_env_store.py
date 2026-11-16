"""Persistent per-user environment-variable storage."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from hermes_constants import get_hermes_home
from utils import atomic_json_write

logger = logging.getLogger(__name__)

CURRENT_USER_NAME_KEY = "CURRENT_USER_NAME"
_STORE_LOCK = threading.RLock()


@dataclass(frozen=True)
class LoadedUserEnv:
    platform: str
    user_id: str
    user_name: str
    user_key: str
    env: dict[str, str]


def get_user_env_path() -> Path:
    return get_hermes_home() / "users.env.json"


def _normalize_identity_component(value: Any) -> str:
    return quote(str(value or "").strip(), safe=".-_~")


def make_user_env_key(platform: Any, user_id: Any, user_name: Any = None) -> str:
    del user_name
    return ".".join(
        (
            _normalize_identity_component(platform),
            _normalize_identity_component(user_id),
        )
    )


def _read_store_unlocked() -> dict[str, dict[str, str]]:
    path = get_user_env_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not read user env store %s: %s", path, exc)
        return {}
    if not isinstance(payload, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for user_key, raw_env in payload.items():
        if not isinstance(user_key, str) or not isinstance(raw_env, dict):
            continue
        result[user_key] = {
            key: str(value)
            for key, value in raw_env.items()
            if isinstance(key, str) and value is not None
        }
    return result


def _write_store_unlocked(payload: dict[str, dict[str, str]]) -> None:
    atomic_json_write(get_user_env_path(), payload, sort_keys=True)


def _normalize_env_payload(raw_env: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(raw_env, dict):
        return {}
    return {
        key: str(value)
        for key, value in raw_env.items()
        if isinstance(key, str) and value is not None
    }


def _with_current_user_name(raw_env: dict[str, Any] | None, user_name: Any) -> dict[str, str]:
    payload = _normalize_env_payload(raw_env)
    payload[CURRENT_USER_NAME_KEY] = str(user_name or "").strip()
    return payload


def load_user_env(platform: Any, user_id: Any, user_name: Any) -> LoadedUserEnv:
    platform_text = str(platform or "").strip()
    user_id_text = str(user_id or "").strip()
    user_name_text = str(user_name or "").strip()
    user_key = make_user_env_key(platform_text, user_id_text)
    legacy_prefix = f"{user_key}."

    with _STORE_LOCK:
        payload = _read_store_unlocked()
        current_env = _normalize_env_payload(payload.get(user_key))
        if current_env:
            hydrated_env = _with_current_user_name(current_env, user_name_text)
            if hydrated_env != current_env:
                payload[user_key] = hydrated_env
                _write_store_unlocked(payload)
            return LoadedUserEnv(platform_text, user_id_text, user_name_text, user_key, hydrated_env)

        legacy_matches = [
            existing_key
            for existing_key, existing_env in payload.items()
            if existing_key.startswith(legacy_prefix) and isinstance(existing_env, dict)
        ]
        if len(legacy_matches) == 1:
            old_key = legacy_matches[0]
            migrated_env = _with_current_user_name(payload.get(old_key), user_name_text)
            payload[user_key] = migrated_env
            del payload[old_key]
            _write_store_unlocked(payload)
            return LoadedUserEnv(platform_text, user_id_text, user_name_text, user_key, migrated_env)
        if len(legacy_matches) > 1:
            logger.warning(
                "Ambiguous user env migration for platform=%r user_id=%r user_name=%r; candidates=%s",
                platform_text,
                user_id_text,
                user_name_text,
                legacy_matches,
            )

    return LoadedUserEnv(
        platform_text,
        user_id_text,
        user_name_text,
        user_key,
        _with_current_user_name({}, user_name_text),
    )


def list_user_env(platform: Any, user_id: Any, user_name: Any) -> LoadedUserEnv:
    return load_user_env(platform, user_id, user_name)


def set_user_env_var(platform: Any, user_id: Any, user_name: Any, key: Any, value: Any) -> LoadedUserEnv:
    env_key = str(key or "").strip()
    if not env_key:
        raise ValueError("Environment variable name is required")
    if "=" in env_key or "\x00" in env_key:
        raise ValueError("Environment variable name cannot contain '=' or NUL bytes")

    loaded = load_user_env(platform, user_id, user_name)
    with _STORE_LOCK:
        payload = _read_store_unlocked()
        current_env = _with_current_user_name(payload.get(loaded.user_key), loaded.user_name)
        if env_key == CURRENT_USER_NAME_KEY:
            current_env[CURRENT_USER_NAME_KEY] = loaded.user_name
        else:
            current_env[env_key] = str(value)
        payload[loaded.user_key] = current_env
        _write_store_unlocked(payload)
    return LoadedUserEnv(loaded.platform, loaded.user_id, loaded.user_name, loaded.user_key, current_env)


def delete_user_env_var(
    platform: Any,
    user_id: Any,
    user_name: Any,
    key: Any,
) -> tuple[LoadedUserEnv, bool]:
    env_key = str(key or "").strip()
    if not env_key:
        raise ValueError("Environment variable name is required")

    loaded = load_user_env(platform, user_id, user_name)
    with _STORE_LOCK:
        payload = _read_store_unlocked()
        current_env = _with_current_user_name(payload.get(loaded.user_key), loaded.user_name)
        deleted = env_key in current_env and env_key != CURRENT_USER_NAME_KEY
        if env_key != CURRENT_USER_NAME_KEY:
            current_env.pop(env_key, None)
        payload[loaded.user_key] = current_env
        _write_store_unlocked(payload)
    return LoadedUserEnv(loaded.platform, loaded.user_id, loaded.user_name, loaded.user_key, current_env), deleted
