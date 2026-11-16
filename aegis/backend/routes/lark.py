"""Lark SSO login routes."""

from __future__ import annotations

import hmac
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from aegis.backend.config import AegisSettings
from aegis.backend.routes.sso import SSO_TICKET_COOKIE
from aegis.backend.services.lark_service import (
    LarkProtocolError,
    complete_lark_callback,
    create_lark_login_transaction,
    lark_is_configured,
)
from aegis.backend.services.oidc_service import utc_timestamp
from aegis.backend.services.user_service import UserService
from aegis.backend.services.user_store import AegisUserStore


def build_lark_sso_router(
    settings: AegisSettings,
    user_service: UserService,
    store: AegisUserStore,
) -> APIRouter:
    router = APIRouter(prefix="/api/lark", tags=["lark-sso"])
    lark_state_cookie = "aegis_lark_state"

    def _post_login_redirect() -> str:
        target = settings.oidc_post_login_redirect
        if not target.startswith("/") or target.startswith("//") or "\\" in target:
            raise HTTPException(status_code=500, detail="Post-login redirect must be a local path.")
        return target

    def _error_redirect(error: str, description: str, *, clear_state: bool = False):
        try:
            location = f"{_post_login_redirect()}?{urlencode({'error': error, 'error_description': description})}"
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        response = RedirectResponse(location, status_code=302, headers={"Cache-Control": "no-store"})
        if clear_state:
            response.delete_cookie(lark_state_cookie, path="/")
        return response

    @router.get("/start")
    async def start():
        if not settings.lark_sso_enabled:
            return JSONResponse({"detail": "Lark SSO is disabled."}, status_code=503)
        if not lark_is_configured(settings):
            return JSONResponse({"detail": "Lark SSO is not configured."}, status_code=503)
        try:
            _post_login_redirect()
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        location, _state = create_lark_login_transaction(store, settings)
        response = RedirectResponse(location, status_code=302, headers={"Cache-Control": "no-store"})
        response.set_cookie(
            lark_state_cookie,
            _state,
            max_age=300,
            httponly=True,
            samesite="lax",
            secure=settings.lark_redirect_uri.startswith("https://"),
            path="/",
        )
        return response

    @router.get("/callback")
    async def callback(
        request: Request,
        code: str = "",
        state: str = "",
        error: str = "",
        error_description: str = "",
    ):
        state_cookie = request.cookies.get(lark_state_cookie, "")
        if not state or not hmac.compare_digest(state_cookie, state):
            return _error_redirect("invalid_request", "Lark login state is invalid or expired.")
        if error:
            now = utc_timestamp()
            transaction = store.claim_lark_login_transaction(state, settings.lark_app_id, now, now)
            if not transaction:
                return _error_redirect("invalid_request", "Lark login transaction was already used.")
            return _error_redirect(
                error,
                error_description or "Lark authorization was denied.",
                clear_state=True,
            )
        if not code:
            now = utc_timestamp()
            if not store.claim_lark_login_transaction(state, settings.lark_app_id, now, now):
                return _error_redirect("invalid_request", "Lark login transaction is invalid or expired.")
            return _error_redirect(
                "invalid_request",
                "Lark callback is missing code or state.",
                clear_state=True,
            )
        try:
            raw_ticket = await complete_lark_callback(
                store,
                user_service,
                settings,
                state=state,
                code=code,
            )
        except (LarkProtocolError, HTTPException) as exc:
            description = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
            return _error_redirect("access_denied", description, clear_state=True)

        response = RedirectResponse(
            _post_login_redirect(),
            status_code=302,
            headers={"Cache-Control": "no-store"},
        )
        response.set_cookie(
            SSO_TICKET_COOKIE,
            raw_ticket,
            max_age=120,
            httponly=True,
            samesite="lax",
            secure=settings.lark_redirect_uri.startswith("https://"),
            path="/",
        )
        response.delete_cookie(lark_state_cookie, path="/")
        return response

    return router
