"""Lark OAuth login helpers for Aegis."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException

from aegis.backend.config import AegisSettings
from aegis.backend.services.oidc_service import (
    create_sso_login_ticket,
    pkce_challenge,
    utc_timestamp,
)
from aegis.backend.services.user_service import UserService
from aegis.backend.services.user_store import AegisUserStore


LARK_AUTHORIZE_URL = "https://accounts.larksuite.com/open-apis/authen/v1/authorize"
LARK_TOKEN_URL = "https://open.larksuite.com/open-apis/authen/v2/oauth/token"
LARK_USER_INFO_URL = "https://open.larksuite.com/open-apis/authen/v1/user_info"
LARK_EMAIL_SCOPE = "contact:user.email:readonly"
LARK_TRANSACTION_TTL_SECONDS = 300


class LarkProtocolError(Exception):
    """Raised when a Lark OAuth response cannot be trusted."""


def lark_is_configured(settings: AegisSettings) -> bool:
    return bool(settings.lark_app_id and settings.lark_app_secret and settings.lark_redirect_uri)


def _require_configured(settings: AegisSettings) -> None:
    if not lark_is_configured(settings):
        raise HTTPException(status_code=503, detail="Lark SSO is not configured.")
    if not settings.lark_redirect_uri.startswith(("http://", "https://")):
        raise HTTPException(status_code=500, detail="Lark redirect URI must be an absolute HTTP URL.")


def create_lark_login_transaction(
    store: AegisUserStore,
    settings: AegisSettings,
) -> tuple[str, str]:
    _require_configured(settings)

    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(48)
    now = datetime.now(UTC)
    store.create_lark_login_transaction(
        {
            "state": state,
            "app_id": settings.lark_app_id,
            "code_verifier": code_verifier,
            "expires_at": utc_timestamp(now + timedelta(seconds=LARK_TRANSACTION_TTL_SECONDS)),
            "created_at": utc_timestamp(now),
        }
    )
    query = {
        "response_type": "code",
        "client_id": settings.lark_app_id,
        "redirect_uri": settings.lark_redirect_uri,
        "scope": LARK_EMAIL_SCOPE,
        "state": state,
        "code_challenge": pkce_challenge(code_verifier),
        "code_challenge_method": "S256",
    }
    return f"{LARK_AUTHORIZE_URL}?{urlencode(query)}", state


async def _exchange_code(
    client: httpx.AsyncClient,
    settings: AegisSettings,
    *,
    code: str,
    code_verifier: str,
) -> str:
    try:
        response = await client.post(
            LARK_TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": settings.lark_app_id,
                "client_secret": settings.lark_app_secret,
                "code": code,
                "redirect_uri": settings.lark_redirect_uri,
                "code_verifier": code_verifier,
            },
        )
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise LarkProtocolError("Lark token exchange failed.") from exc

    if (
        response.status_code != 200
        or not isinstance(payload, dict)
        or str(payload.get("code", "")) != "0"
        or not payload.get("access_token")
    ):
        raise LarkProtocolError("Lark token exchange failed.")
    return str(payload["access_token"])


async def _get_user_email(
    client: httpx.AsyncClient,
    access_token: str,
) -> str:
    try:
        response = await client.get(
            LARK_USER_INFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise LarkProtocolError("Lark user information request failed.") from exc

    if response.status_code != 200 or not isinstance(payload, dict) or str(payload.get("code", "")) != "0":
        raise LarkProtocolError("Lark user information request failed.")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise LarkProtocolError("Lark user information is missing.")
    email = str(data.get("email") or data.get("enterprise_email") or "").strip()
    if not email:
        raise LarkProtocolError("Lark user email is missing.")
    return email


async def complete_lark_callback(
    store: AegisUserStore,
    user_service: UserService,
    settings: AegisSettings,
    *,
    state: str,
    code: str,
) -> str:
    _require_configured(settings)
    now = utc_timestamp()
    transaction = store.claim_lark_login_transaction(
        state,
        settings.lark_app_id,
        now,
        now,
    )
    if not transaction:
        raise LarkProtocolError("Lark login transaction is invalid or expired.")

    async with httpx.AsyncClient(timeout=10) as client:
        access_token = await _exchange_code(
            client,
            settings,
            code=code,
            code_verifier=transaction["code_verifier"],
        )
        email = await _get_user_email(client, access_token)

    user = user_service.upsert_lark_user(email=email)
    return create_sso_login_ticket(store, user)
