"""WORKAGENT auth helpers."""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, WebSocketException

from workagent.backend.config import WorkagentSettings


AUTH_HEADER = "Authorization"


def extract_bearer_token(request: Request) -> str | None:
    """Extract the bearer token from an HTTP request."""
    auth_header = (request.headers.get(AUTH_HEADER) or "").strip()
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[len("Bearer ") :].strip()
    return token or None


def token_matches(token: str | None, settings: WorkagentSettings) -> bool:
    """Return True when a candidate token matches the configured session token."""
    return token_matches_value(token, settings.session_token)


def token_matches_value(token: str | None, expected_token: str | None) -> bool:
    """Return True when a candidate token matches the expected token."""
    if not token or not expected_token:
        return False
    return hmac.compare_digest(token.encode(), expected_token.encode())


def verify_bearer_token(request: Request, settings: WorkagentSettings) -> None:
    """Validate request bearer token or raise 401."""
    if not token_matches(extract_bearer_token(request), settings):
        raise HTTPException(status_code=401, detail="Unauthorized")


def verify_bearer_token_value(request: Request, expected_token: str | None) -> None:
    """Validate request bearer token against an explicit token or raise 401."""
    if not token_matches_value(extract_bearer_token(request), expected_token):
        raise HTTPException(status_code=401, detail="Unauthorized")


def verify_ws_token(token: str | None, settings: WorkagentSettings) -> None:
    """Validate websocket token or raise websocket unauthorized."""
    if not token_matches(token, settings):
        raise WebSocketException(code=4401, reason="Unauthorized")
