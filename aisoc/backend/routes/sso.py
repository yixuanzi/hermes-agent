"""AISOC OIDC client routes."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from aisoc.backend.config import AisocSettings
from aisoc.backend.models import AuthLoginResponse
from aisoc.backend.services.oidc_service import (
    OidcProtocolError,
    complete_callback,
    create_login_transaction,
    exchange_ticket,
    oidc_is_configured,
)
from aisoc.backend.services.user_service import UserService
from aisoc.backend.services.user_store import AisocUserStore


SSO_TICKET_COOKIE = "aisoc_sso_ticket"


def build_sso_router(
    settings: AisocSettings,
    user_service: UserService,
    store: AisocUserStore,
) -> APIRouter:
    router = APIRouter(prefix="/api/sso", tags=["sso"])

    def _error_redirect(error: str, description: str) -> RedirectResponse:
        location = f"{settings.oidc_post_login_redirect}?{urlencode({'error': error, 'error_description': description})}"
        return RedirectResponse(location, status_code=302, headers={"Cache-Control": "no-store"})

    @router.get("/start")
    async def start(request: Request, organization_id: str = "", client_id: str = "", sso: str = ""):
        if not oidc_is_configured(settings):
            return JSONResponse({"detail": "OIDC is not configured."}, status_code=503)
        if client_id and client_id != settings.oidc_client_id:
            return JSONResponse({"detail": "OIDC client_id does not match this application."}, status_code=400)
        if sso == "1":
            location, _state = create_login_transaction(
                store,
                settings,
                flow="sso",
                organization_id=None,
            )
        else:
            if not organization_id or not client_id:
                return JSONResponse(
                    {"detail": "organization_id and client_id are required for direct OIDC login."},
                    status_code=400,
                )
            location, _state = create_login_transaction(
                store,
                settings,
                flow="direct",
                organization_id=organization_id,
            )
        return RedirectResponse(location, status_code=302, headers={"Cache-Control": "no-store"})

    @router.get("/callback")
    async def callback(request: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""):
        if error:
            return _error_redirect(error, error_description or "OIDC authorization failed.")
        if not code or not state:
            return _error_redirect("invalid_request", "OIDC callback is missing code or state.")
        try:
            raw_ticket = await complete_callback(
                store,
                user_service,
                settings,
                state=state,
                code=code,
            )
        except (OidcProtocolError, HTTPException) as exc:
            if isinstance(exc, HTTPException):
                return _error_redirect("access_denied", str(exc.detail))
            return _error_redirect("access_denied", str(exc))
        response = RedirectResponse(settings.oidc_post_login_redirect, status_code=302, headers={"Cache-Control": "no-store"})
        response.set_cookie(
            SSO_TICKET_COOKIE,
            raw_ticket,
            max_age=120,
            httponly=True,
            samesite="lax",
            secure=settings.oidc_redirect_uri.startswith("https://"),
            path="/",
        )
        return response

    @router.post("/exchange", response_model=AuthLoginResponse)
    async def exchange(request: Request) -> AuthLoginResponse:
        raw_ticket = request.cookies.get(SSO_TICKET_COOKIE, "")
        access_token, (user, expires_in) = exchange_ticket(store, user_service, settings, raw_ticket)
        response = JSONResponse(
            AuthLoginResponse(
                authenticated=True,
                access_token=access_token,
                expires_in=expires_in,
                user=user,
            ).model_dump()
        )
        response.delete_cookie(SSO_TICKET_COOKIE, path="/")
        return response

    return router
