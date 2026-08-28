"""AISOC backend configuration helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import secrets
from typing import Literal


A2ATokenSource = Literal["disabled", "env", "generated"]


@dataclass(frozen=True)
class AisocSettings:
    host: str = "127.0.0.1"
    port: int = 9120
    open_browser: bool = True
    allow_public: bool = False
    jwt_secret: str = ""
    jwt_expire_seconds: int = 28800
    a2a_auth_enabled: bool = False
    a2a_session_token: str = ""
    a2a_token_source: A2ATokenSource = "disabled"
    a2a_admin_token: str = ""
    oidc_issuer: str = "http://127.0.0.1:8080"
    oidc_backchannel_url: str = "http://127.0.0.1:8080"
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = "http://127.0.0.1:9120/api/sso/callback"
    oidc_post_login_redirect: str = "/sso/callback"
    dist_dir: Path | None = None


def _env_flag(name: str) -> bool:
    value = (os.environ.get(name) or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def load_aisoc_settings(
    *,
    host: str = "127.0.0.1",
    port: int = 9120,
    open_browser: bool = True,
    allow_public: bool = False,
    dist_dir: Path | None = None,
) -> AisocSettings:
    """Load settings from explicit args plus environment fallback."""
    jwt_secret = (os.environ.get("AISOC_JWT_SECRET") or "").strip() or secrets.token_urlsafe(32)

    a2a_auth_enabled = _env_flag("AISOC_A2A_AUTH")
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

    a2a_admin_token = (os.environ.get("AISOC_A2A_ADMIN_TOKEN") or "").strip()

    oidc_issuer = (os.environ.get("AISOC_OIDC_ISSUER") or "http://127.0.0.1:8080").strip().rstrip("/")
    oidc_backchannel_url = (
        os.environ.get("AISOC_OIDC_BACKCHANNEL_URL") or oidc_issuer
    ).strip().rstrip("/")
    oidc_redirect_uri = (
        os.environ.get("AISOC_OIDC_REDIRECT_URI")
        or "http://127.0.0.1:9120/api/sso/callback"
    ).strip()
    oidc_post_login_redirect = (
        os.environ.get("AISOC_OIDC_POST_LOGIN_REDIRECT") or "/sso/callback"
    ).strip()

    return AisocSettings(
        host=host,
        port=port,
        open_browser=open_browser,
        allow_public=allow_public,
        jwt_secret=jwt_secret,
        jwt_expire_seconds=28800,
        a2a_auth_enabled=a2a_auth_enabled,
        a2a_session_token=a2a_token,
        a2a_token_source=a2a_source,
        a2a_admin_token=a2a_admin_token,
        oidc_issuer=oidc_issuer,
        oidc_backchannel_url=oidc_backchannel_url,
        oidc_client_id=(os.environ.get("AISOC_OIDC_CLIENT_ID") or "").strip(),
        oidc_client_secret=(os.environ.get("AISOC_OIDC_CLIENT_SECRET") or "").strip(),
        oidc_redirect_uri=oidc_redirect_uri,
        oidc_post_login_redirect=oidc_post_login_redirect,
        dist_dir=dist_dir,
    )


def is_loopback_host(host: str) -> bool:
    """Return True when host binds loopback only."""
    normalized = (host or "").strip().lower()
    return normalized in {"127.0.0.1", "localhost", "::1"}
