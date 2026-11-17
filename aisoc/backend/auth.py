"""AISOC auth helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import jwt
from fastapi import HTTPException, Request
from jwt import ExpiredSignatureError, InvalidTokenError

from aisoc.backend.config import AisocSettings
from aisoc.backend.models import UserResponse

if TYPE_CHECKING:
    from aisoc.backend.services.user_service import UserService


AUTH_HEADER = "Authorization"
JWT_ALGORITHM = "HS256"
PBKDF2_ITERATIONS = 310_000


def extract_bearer_token(request: Request) -> str | None:
    """Extract the bearer token from an HTTP request."""
    auth_header = (request.headers.get(AUTH_HEADER) or "").strip()
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[len("Bearer ") :].strip()
    return token or None


def token_matches_value(token: str | None, expected_token: str | None) -> bool:
    """Return True when a candidate token matches the expected token."""
    if not token or not expected_token:
        return False
    return hmac.compare_digest(token.encode(), expected_token.encode())


def verify_bearer_token_value(request: Request, expected_token: str | None) -> None:
    """Validate request bearer token against an explicit token or raise 401."""
    if not token_matches_value(extract_bearer_token(request), expected_token):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, digest = str(encoded or "").split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            _b64decode(salt),
            int(iterations),
        )
        return hmac.compare_digest(_b64encode(candidate), digest)
    except Exception:
        return False


def create_access_token(user: UserResponse, settings: AisocSettings, *, token_version: int) -> str:
    """``ver`` ties the token to the user's current token version (bumped on
    password change and disable); ``jti`` lets logout revoke just this one."""
    now = _utc_now()
    payload = {
        "sub": user.uid,
        "username": user.username,
        "email": user.email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.jwt_expire_seconds)).timestamp()),
        "ver": token_version,
        "jti": uuid4().hex,
    }
    return str(jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM))


def decode_access_token(token: str, settings: AisocSettings) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALGORITHM])
    except ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Token has expired.") from exc
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Unauthorized") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not payload.get("sub"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return payload


def build_token_payload(
    user: UserResponse, settings: AisocSettings, user_service: UserService
) -> tuple[str, int]:
    token = create_access_token(user, settings, token_version=user_service.token_version(user.uid))
    return token, settings.jwt_expire_seconds


def expires_in_from_payload(payload: dict[str, Any]) -> int:
    exp = int(payload.get("exp") or 0)
    remaining = exp - int(_utc_now().timestamp())
    return max(0, remaining)


def get_current_user_from_token(
    token: str,
    settings: AisocSettings,
    user_service: UserService,
) -> tuple[UserResponse, dict[str, Any]]:
    payload = decode_access_token(token, settings)
    return user_service.get_user_for_token(payload), payload


def get_optional_user_from_request(
    request: Request,
    settings: AisocSettings,
    user_service: UserService,
) -> tuple[UserResponse | None, dict[str, Any] | None]:
    token = extract_bearer_token(request)
    if not token:
        return None, None
    try:
        return get_current_user_from_token(token, settings, user_service)
    except HTTPException:
        return None, None


def require_authenticated_user(
    request: Request,
    settings: AisocSettings,
    user_service: UserService,
) -> tuple[UserResponse, dict[str, Any]]:
    token = extract_bearer_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return get_current_user_from_token(token, settings, user_service)


def require_admin_user(user: UserResponse) -> None:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required.")
