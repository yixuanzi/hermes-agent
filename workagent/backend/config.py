"""WORKAGENT backend configuration helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import secrets
from typing import Literal


TokenSource = Literal["env", "generated"]
A2ATokenSource = Literal["disabled", "env", "generated"]


@dataclass(frozen=True)
class WorkagentSettings:
    host: str = "127.0.0.1"
    port: int = 9120
    open_browser: bool = True
    allow_public: bool = False
    embedded_chat: bool = False
    session_token: str = ""
    token_source: TokenSource = "generated"
    a2a_auth_enabled: bool = False
    a2a_session_token: str = ""
    a2a_token_source: A2ATokenSource = "disabled"
    a2a_admin_token: str = ""
    dist_dir: Path | None = None


def _env_flag(name: str) -> bool:
    value = (os.environ.get(name) or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def load_workagent_settings(
    *,
    host: str = "127.0.0.1",
    port: int = 9120,
    open_browser: bool = True,
    allow_public: bool = False,
    embedded_chat: bool = False,
    dist_dir: Path | None = None,
) -> WorkagentSettings:
    """Load settings from explicit args plus environment fallback."""
    env_token = (os.environ.get("WORKAGENT_SESSION_TOKEN") or "").strip()
    if env_token:
        token = env_token
        source: TokenSource = "env"
    else:
        token = secrets.token_urlsafe(32)
        source = "generated"

    a2a_auth_enabled = _env_flag("WORKAGENT_A2A_AUTH")
    if a2a_auth_enabled:
        env_a2a_token = (os.environ.get("A2A_SESSION_TOKEN") or "").strip()
        if env_a2a_token:
            a2a_token = env_a2a_token
            a2a_source: A2ATokenSource = "env"
        else:
            a2a_token = secrets.token_urlsafe(32)
            a2a_source = "generated"
    else:
        a2a_token = ""
        a2a_source = "disabled"

    a2a_admin_token = (os.environ.get("WORKAGENT_A2A_ADMIN_TOKEN") or "").strip()

    return WorkagentSettings(
        host=host,
        port=port,
        open_browser=open_browser,
        allow_public=allow_public,
        embedded_chat=embedded_chat,
        session_token=token,
        token_source=source,
        a2a_auth_enabled=a2a_auth_enabled,
        a2a_session_token=a2a_token,
        a2a_token_source=a2a_source,
        a2a_admin_token=a2a_admin_token,
        dist_dir=dist_dir,
    )


def is_loopback_host(host: str) -> bool:
    """Return True when host binds loopback only."""
    normalized = (host or "").strip().lower()
    return normalized in {"127.0.0.1", "localhost", "::1"}
