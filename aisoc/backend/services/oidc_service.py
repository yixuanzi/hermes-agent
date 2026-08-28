"""OIDC client helpers for AISOC."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import HTTPException

from aisoc.backend.config import AisocSettings
from aisoc.backend.services.user_service import UserService
from aisoc.backend.services.user_store import AisocUserStore


OIDC_SCOPE = "openid profile email"
OIDC_TRANSACTION_TTL_SECONDS = 300
SSO_TICKET_TTL_SECONDS = 120


class OidcProtocolError(Exception):
    """Raised when the upstream OIDC response fails validation."""


def utc_timestamp(value: datetime | None = None) -> str:
    current = value or datetime.now(UTC)
    return current.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def pkce_challenge(verifier: str) -> str:
    encoded = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode("ascii")


def oidc_is_configured(settings: AisocSettings) -> bool:
    return bool(settings.oidc_client_id and settings.oidc_client_secret)


def _require_configured(settings: AisocSettings) -> None:
    if not oidc_is_configured(settings):
        raise HTTPException(status_code=503, detail="OIDC is not configured.")
    if not settings.oidc_post_login_redirect.startswith("/") or settings.oidc_post_login_redirect.startswith("//"):
        raise HTTPException(status_code=500, detail="OIDC post-login redirect must be a local path.")


def create_login_transaction(
    store: AisocUserStore,
    settings: AisocSettings,
    *,
    flow: str,
    organization_id: str | None,
) -> tuple[str, str]:
    _require_configured(settings)
    if flow not in {"direct", "sso"}:
        raise ValueError("Unsupported OIDC flow")
    if flow == "direct" and not organization_id:
        raise HTTPException(status_code=400, detail="organization_id is required for direct OIDC login.")
    if flow == "sso":
        organization_id = None

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(48)
    now = datetime.now(UTC)
    store.create_oidc_login_transaction(
        {
            "state": state,
            "nonce": nonce,
            "code_verifier": code_verifier,
            "client_id": settings.oidc_client_id,
            "organization_id": organization_id,
            "flow": flow,
            "expires_at": utc_timestamp(now + timedelta(seconds=OIDC_TRANSACTION_TTL_SECONDS)),
            "created_at": utc_timestamp(now),
        }
    )
    query: dict[str, str] = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": settings.oidc_redirect_uri,
        "scope": OIDC_SCOPE,
        "state": state,
        "nonce": nonce,
        "code_challenge": pkce_challenge(code_verifier),
        "code_challenge_method": "S256",
    }
    if flow == "direct":
        query["organization_id"] = str(organization_id)
    else:
        query["sso"] = "1"
    return f"{settings.oidc_issuer}/oauth/authorize?{urlencode(query)}", state


async def _exchange_code(
    client: httpx.AsyncClient,
    settings: AisocSettings,
    *,
    code: str,
    code_verifier: str,
) -> dict[str, Any]:
    try:
        response = await client.post(
            f"{settings.oidc_backchannel_url}/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.oidc_redirect_uri,
                "code_verifier": code_verifier,
            },
            auth=(settings.oidc_client_id, settings.oidc_client_secret),
        )
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OidcProtocolError("Portal token exchange failed.") from exc
    if response.status_code != 200 or not isinstance(payload, dict) or not payload.get("id_token") or not payload.get("access_token"):
        raise OidcProtocolError("Portal token exchange failed.")
    return payload


async def _verify_id_token(
    client: httpx.AsyncClient,
    settings: AisocSettings,
    id_token: str,
    expected_nonce: str,
) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(id_token)
        jwks_response = await client.get(f"{settings.oidc_backchannel_url}/.well-known/jwks.json")
        jwks = jwks_response.json()
        keys = jwks.get("keys", []) if isinstance(jwks, dict) else []
        key_data = next((item for item in keys if item.get("kid") == header.get("kid")), None)
        if key_data is None and len(keys) == 1:
            key_data = keys[0]
        if not key_data:
            raise OidcProtocolError("Portal signing key was not found.")
        signing_key = jwt.PyJWK(key_data).key
        claims = jwt.decode(
            id_token,
            signing_key,
            algorithms=["RS256"],
            audience=settings.oidc_client_id,
            issuer=settings.oidc_issuer,
            options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]},
        )
    except OidcProtocolError:
        raise
    except (httpx.HTTPError, ValueError, jwt.PyJWTError, KeyError, TypeError) as exc:
        raise OidcProtocolError("ID Token validation failed.") from exc
    if not hmac.compare_digest(str(claims.get("nonce", "")), expected_nonce):
        raise OidcProtocolError("ID Token nonce validation failed.")
    return claims


async def _get_userinfo(
    client: httpx.AsyncClient,
    settings: AisocSettings,
    access_token: str,
) -> dict[str, Any]:
    try:
        response = await client.get(
            f"{settings.oidc_backchannel_url}/oauth/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OidcProtocolError("Portal UserInfo request failed.") from exc
    if response.status_code != 200 or not isinstance(payload, dict):
        raise OidcProtocolError("Portal UserInfo request failed.")
    return payload


async def complete_callback(
    store: AisocUserStore,
    user_service: UserService,
    settings: AisocSettings,
    *,
    state: str,
    code: str,
) -> str:
    _require_configured(settings)
    transaction = store.get_oidc_login_transaction(state, utc_timestamp())
    if not transaction or transaction["client_id"] != settings.oidc_client_id:
        raise OidcProtocolError("OIDC login transaction is invalid or expired.")

    async with httpx.AsyncClient(timeout=10) as client:
        tokens = await _exchange_code(
            client,
            settings,
            code=code,
            code_verifier=transaction["code_verifier"],
        )
        claims = await _verify_id_token(
            client,
            settings,
            str(tokens["id_token"]),
            str(transaction["nonce"]),
        )
        userinfo = await _get_userinfo(client, settings, str(tokens["access_token"]))

    subject = str(claims.get("sub") or "")
    userinfo_subject = str(userinfo.get("sub") or "")
    organization_id = str(claims.get("organization_id") or "")
    userinfo_organization_id = str(userinfo.get("organization_id") or "")
    email = str(userinfo.get("email") or claims.get("email") or "").strip()
    if not subject or not userinfo_subject or subject != userinfo_subject:
        raise OidcProtocolError("OIDC subject validation failed.")
    if not organization_id or organization_id != userinfo_organization_id:
        raise OidcProtocolError("OIDC organization validation failed.")
    if not email:
        raise OidcProtocolError("OIDC email claim is required.")
    if transaction["flow"] == "direct" and str(transaction["organization_id"]) != organization_id:
        raise OidcProtocolError("OIDC organization context validation failed.")
    if not store.consume_oidc_login_transaction(state, utc_timestamp()):
        raise OidcProtocolError("OIDC login transaction was already used.")

    user = user_service.upsert_oidc_user(subject=subject, email=email)
    return create_sso_login_ticket(store, user)


def create_sso_login_ticket(store: AisocUserStore, user: Any) -> str:
    """Issue the shared short-lived ticket used by all external SSO providers."""
    raw_ticket = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    store.create_sso_login_ticket(
        {
            "ticket_digest": digest(raw_ticket),
            "user_uid": user.uid,
            "expires_at": utc_timestamp(now + timedelta(seconds=SSO_TICKET_TTL_SECONDS)),
            "created_at": utc_timestamp(now),
        }
    )
    return raw_ticket


def exchange_ticket(
    store: AisocUserStore,
    user_service: UserService,
    settings: AisocSettings,
    raw_ticket: str,
) -> tuple[str, Any]:
    if not raw_ticket:
        raise HTTPException(status_code=401, detail="SSO login ticket is missing.")
    row = store.claim_sso_login_ticket(digest(raw_ticket), utc_timestamp(), utc_timestamp())
    if not row:
        raise HTTPException(status_code=401, detail="SSO login ticket is invalid, expired, or already used.")
    user = user_service.get_user_by_uid(row["user_uid"])
    if user.status != "enabled":
        raise HTTPException(status_code=403, detail="User account is disabled.")
    from aisoc.backend.auth import build_token_payload

    access_token, expires_in = build_token_payload(user, settings)
    return access_token, (user, expires_in)
