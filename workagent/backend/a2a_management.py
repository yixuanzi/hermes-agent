"""The ``/man`` management console of the WORKAGENT A2A module.

Admin-only: every API call needs a short-lived access token issued in
exchange for ``WORKAGENT_A2A_ADMIN_TOKEN`` (never the A2A session token).  It
offers two things:

* personal keys — create, list, edit (name, identity, enabled), regenerate and
  delete the keys the A2A route and ``/mcp`` accept besides the A2A token, each
  bound to the ``<source>`` identity prepended to its tasks
  (``a2a_service.mcp_keys``, enforced by ``a2a_service.key_scope``);
* process control — the guarded self-restart.

The A2A bearer middleware skips these paths; they authenticate themselves here.
"""

from __future__ import annotations

from _thread import interrupt_main as _interrupt_main
import secrets
import time
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from hermes_self_restart import request_self_restart
from workagent.backend.a2a_management_page import render_management_page
from workagent.backend.a2a_service.mcp_keys import McpKeyError, McpKeyStore
from workagent.backend.auth import extract_bearer_token, token_matches_value
from workagent.backend.config import WorkagentSettings


MANAGEMENT_PREFIX = "/man"
MANAGEMENT_TOKEN_TTL_SECONDS = 300
MANAGEMENT_UNAVAILABLE_DETAIL = "A2A management is not enabled"
_NO_STORE = {"Cache-Control": "no-store"}


def is_management_path(path: str) -> bool:
    return path == MANAGEMENT_PREFIX or path.startswith(f"{MANAGEMENT_PREFIX}/")


class _ManagementAuthRequest(BaseModel):
    token: str


class ManagementAccessTokens:
    """Process-local, short-lived bearer credentials for A2A management."""

    def __init__(self) -> None:
        self._tokens: dict[str, float] = {}

    def issue(self) -> str:
        now = time.monotonic()
        self._discard_expired(now)
        token = secrets.token_urlsafe(32)
        self._tokens[token] = now + MANAGEMENT_TOKEN_TTL_SECONDS
        return token

    def validates(self, candidate: str | None) -> bool:
        now = time.monotonic()
        self._discard_expired(now)
        if not candidate:
            return False
        return any(token_matches_value(candidate, issued) for issued in self._tokens)

    def _discard_expired(self, now: float) -> None:
        self._tokens = {
            token: expires_at for token, expires_at in self._tokens.items() if expires_at > now
        }


def _graceful_shutdown() -> None:
    """Ask Python's main thread to stop after FastAPI sends the response."""
    _interrupt_main()


def _page_headers(nonce: str) -> dict[str, str]:
    content_security_policy = "; ".join(
        (
            "default-src 'none'",
            f"style-src 'nonce-{nonce}'",
            f"script-src 'nonce-{nonce}'",
            "connect-src 'self'",
            "frame-ancestors 'none'",
            "base-uri 'none'",
            "form-action 'none'",
        )
    )
    return {
        **_NO_STORE,
        "Content-Security-Policy": content_security_policy,
        "X-Frame-Options": "DENY",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }


async def _json_object(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="request body must be JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="request body must be an object")
    return payload


def _key_error(exc: McpKeyError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def register_management_routes(
    app: FastAPI,
    settings: WorkagentSettings,
    key_store: McpKeyStore,
    *,
    a2a_path: str,
    mcp_path: str,
) -> None:
    """Mount ``/man`` and its admin API on ``app``."""
    access_tokens = ManagementAccessTokens()

    def _require_enabled() -> None:
        if not settings.a2a_admin_token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=MANAGEMENT_UNAVAILABLE_DETAIL,
            )

    def _require_access(request: Request) -> None:
        _require_enabled()
        if not access_tokens.validates(extract_bearer_token(request)):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    @app.get(MANAGEMENT_PREFIX, response_class=HTMLResponse, include_in_schema=False)
    async def management_page() -> HTMLResponse:
        nonce = secrets.token_urlsafe(24)
        return HTMLResponse(
            render_management_page(
                enabled=bool(settings.a2a_admin_token),
                nonce=nonce,
                a2a_path=a2a_path,
                mcp_path=mcp_path,
            ),
            headers=_page_headers(nonce),
        )

    @app.post(f"{MANAGEMENT_PREFIX}/api/auth", include_in_schema=False)
    async def management_auth(payload: _ManagementAuthRequest) -> JSONResponse:
        _require_enabled()
        # The A2A session token is a client credential, never an admin one,
        # even if an operator configured both to the same value.
        if token_matches_value(payload.token, settings.a2a_session_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
        if not token_matches_value(payload.token, settings.a2a_admin_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
        return JSONResponse(
            {
                "access_token": access_tokens.issue(),
                "token_type": "bearer",
                "expires_in": MANAGEMENT_TOKEN_TTL_SECONDS,
                "expires_at": int(time.time() + MANAGEMENT_TOKEN_TTL_SECONDS),
            },
            headers=_NO_STORE,
        )

    @app.post(
        f"{MANAGEMENT_PREFIX}/api/restart",
        status_code=status.HTTP_202_ACCEPTED,
        include_in_schema=False,
    )
    async def management_restart(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
        _require_access(request)
        result = request_self_restart(
            "workagent-a2a",
            lambda: background_tasks.add_task(_graceful_shutdown),
        )
        return JSONResponse(result.as_dict(), status_code=status.HTTP_202_ACCEPTED, headers=_NO_STORE)

    # ── personal keys ───────────────────────────────────────────────────
    keys_path = f"{MANAGEMENT_PREFIX}/api/keys"

    @app.get(keys_path, include_in_schema=False)
    async def list_keys(request: Request) -> JSONResponse:
        _require_access(request)
        return JSONResponse({"keys": key_store.list()}, headers=_NO_STORE)

    @app.post(keys_path, status_code=status.HTTP_201_CREATED, include_in_schema=False)
    async def create_key(request: Request) -> JSONResponse:
        _require_access(request)
        payload = await _json_object(request)
        try:
            record, secret = key_store.create(name=payload.get("name"), source=payload.get("source"))
        except McpKeyError as exc:
            raise _key_error(exc) from exc
        return JSONResponse(
            {"key": record, "secret": secret},
            status_code=status.HTTP_201_CREATED,
            headers=_NO_STORE,
        )

    @app.get(f"{keys_path}/{{key_id}}", include_in_schema=False)
    async def get_key(key_id: str, request: Request) -> JSONResponse:
        _require_access(request)
        try:
            return JSONResponse({"key": key_store.get(key_id)}, headers=_NO_STORE)
        except McpKeyError as exc:
            raise _key_error(exc) from exc

    @app.patch(f"{keys_path}/{{key_id}}", include_in_schema=False)
    async def update_key(key_id: str, request: Request) -> JSONResponse:
        _require_access(request)
        payload = await _json_object(request)
        try:
            return JSONResponse({"key": key_store.update(key_id, payload)}, headers=_NO_STORE)
        except McpKeyError as exc:
            raise _key_error(exc) from exc

    @app.post(f"{keys_path}/{{key_id}}/regenerate", include_in_schema=False)
    async def regenerate_key(key_id: str, request: Request) -> JSONResponse:
        _require_access(request)
        try:
            record, secret = key_store.regenerate(key_id)
        except McpKeyError as exc:
            raise _key_error(exc) from exc
        return JSONResponse({"key": record, "secret": secret}, headers=_NO_STORE)

    @app.delete(
        f"{keys_path}/{{key_id}}",
        status_code=status.HTTP_204_NO_CONTENT,
        include_in_schema=False,
    )
    async def delete_key(key_id: str, request: Request) -> Response:
        _require_access(request)
        try:
            key_store.delete(key_id)
        except McpKeyError as exc:
            raise _key_error(exc) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT, headers=_NO_STORE)
