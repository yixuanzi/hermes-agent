"""Auth routes for WORKAGENT backend."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from workagent.backend.auth import extract_bearer_token, token_matches
from workagent.backend.config import WorkagentSettings
from workagent.backend.models import (
    AuthLoginRequest,
    AuthLoginResponse,
    AuthLogoutResponse,
    AuthSessionResponse,
)


def build_auth_router(settings: WorkagentSettings) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.post("/login", response_model=AuthLoginResponse)
    async def login(body: AuthLoginRequest) -> AuthLoginResponse:
        if not token_matches(body.token, settings):
            raise HTTPException(status_code=401, detail="Unauthorized")
        return AuthLoginResponse(authenticated=True)

    @router.get("/session", response_model=AuthSessionResponse)
    async def session(request: Request) -> AuthSessionResponse:
        token = extract_bearer_token(request)
        return AuthSessionResponse(
            authenticated=token_matches(token, settings),
            token_source=settings.token_source,
        )

    @router.post("/logout", response_model=AuthLogoutResponse)
    async def logout() -> AuthLogoutResponse:
        return AuthLogoutResponse(logged_out=True)

    return router

