"""Runtime binding helpers for per-user environment injection."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Iterator
from urllib.parse import quote

from tools.user_env_store import load_user_env, make_user_env_key


@dataclass(frozen=True)
class UserEnvIdentity:
    platform: str
    user_id: str
    user_name: str
    user_key: str

    @property
    def storage_key(self) -> str:
        return self.user_key

    @property
    def runtime_scope_key(self) -> str:
        return make_user_env_runtime_scope_key(self.platform, self.user_id)


_CURRENT_USER_ENV_IDENTITY: ContextVar[UserEnvIdentity | None] = ContextVar(
    "_CURRENT_USER_ENV_IDENTITY",
    default=None,
)


def _clean(value) -> str:
    return str(value or "").strip()


def _runtime_scope_component(value) -> str:
    return quote(_clean(value), safe=".-_~")


def make_user_env_runtime_scope_key(platform, user_id) -> str:
    return f"local::{_runtime_scope_component(platform)}::{_runtime_scope_component(user_id)}"


def set_current_user_env_identity(platform, user_id, user_name, user_key: str = "") -> Token:
    platform_text = _clean(platform)
    user_id_text = _clean(user_id)
    user_name_text = _clean(user_name)
    if not user_id_text:
        return _CURRENT_USER_ENV_IDENTITY.set(None)
    identity = UserEnvIdentity(
        platform=platform_text,
        user_id=user_id_text,
        user_name=user_name_text,
        user_key=user_key or make_user_env_key(platform_text, user_id_text, user_name_text),
    )
    return _CURRENT_USER_ENV_IDENTITY.set(identity)


def reset_current_user_env_identity(token: Token) -> None:
    _CURRENT_USER_ENV_IDENTITY.reset(token)


def get_current_user_env_identity() -> UserEnvIdentity | None:
    identity = _CURRENT_USER_ENV_IDENTITY.get()
    if identity is not None:
        return identity

    try:
        from gateway.session_context import get_session_env
    except Exception:
        return None

    platform = _clean(get_session_env("HERMES_SESSION_PLATFORM", ""))
    user_id = _clean(get_session_env("HERMES_SESSION_USER_ID", ""))
    user_name = _clean(get_session_env("HERMES_SESSION_USER_NAME", ""))
    if not user_id:
        return None
    return UserEnvIdentity(
        platform=platform,
        user_id=user_id,
        user_name=user_name,
        user_key=make_user_env_key(platform, user_id, user_name),
    )


def get_current_user_env_values() -> dict[str, str]:
    identity = get_current_user_env_identity()
    if identity is None:
        return {}
    loaded = load_user_env(identity.platform, identity.user_id, identity.user_name)
    return dict(loaded.env)


@contextmanager
def bind_current_user_env_identity(platform, user_id, user_name) -> Iterator[UserEnvIdentity | None]:
    token = set_current_user_env_identity(platform, user_id, user_name)
    try:
        yield get_current_user_env_identity()
    finally:
        reset_current_user_env_identity(token)


@contextmanager
def bind_current_user_env_identity_from_agent(agent) -> Iterator[UserEnvIdentity | None]:
    platform = getattr(agent, "_user_env_platform", None) or getattr(agent, "platform", None)
    user_id = getattr(agent, "_user_id", None) or getattr(agent, "user_id", None)
    user_name = getattr(agent, "_user_name", None) or getattr(agent, "user_name", None)
    token = set_current_user_env_identity(platform, user_id, user_name)
    try:
        yield get_current_user_env_identity()
    finally:
        reset_current_user_env_identity(token)
