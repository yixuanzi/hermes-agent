"""Auth routes for Aegis backend."""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from aegis.backend.auth import (
    build_token_payload,
    expires_in_from_payload,
    get_optional_user_from_request,
    require_authenticated_user,
)
from aegis.backend.config import AegisSettings
from aegis.backend.models import (
    AuthLoginRequest,
    AuthLoginResponse,
    AuthLogoutResponse,
    AuthPasswordChangeRequest,
    AuthPasswordChangeResponse,
    AuthRegisterRequest,
    AuthRegisterResponse,
    AuthSessionResponse,
)
from aegis.backend.services.user_service import UserService


def build_auth_router(settings: AegisSettings, user_service: UserService) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.post("/login", response_model=AuthLoginResponse)
    async def login(body: AuthLoginRequest) -> AuthLoginResponse:
        user = user_service.authenticate_user(body.username, body.password)
        access_token, expires_in = build_token_payload(user, settings)
        return AuthLoginResponse(
            authenticated=True,
            access_token=access_token,
            expires_in=expires_in,
            user=user,
        )

    @router.post(
        "/register",
        response_model=AuthRegisterResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def register(body: AuthRegisterRequest) -> AuthRegisterResponse:
        user_service.register_user(body)
        return AuthRegisterResponse(registered=True, status="disabled")

    @router.get("/session", response_model=AuthSessionResponse, response_model_exclude_none=True)
    async def session(request: Request) -> AuthSessionResponse:
        user, payload = get_optional_user_from_request(request, settings, user_service)
        if user is None or payload is None:
            return AuthSessionResponse(authenticated=False)
        return AuthSessionResponse(
            authenticated=True,
            user=user,
            expires_in=expires_in_from_payload(payload),
        )

    @router.post("/logout", response_model=AuthLogoutResponse)
    async def logout() -> AuthLogoutResponse:
        return AuthLogoutResponse(logged_out=True)

    @router.put("/password", response_model=AuthPasswordChangeResponse)
    async def change_password(
        body: AuthPasswordChangeRequest,
        request: Request,
    ) -> AuthPasswordChangeResponse:
        user, _payload = require_authenticated_user(request, settings, user_service)
        user_service.change_password(user.uid, body.old_password, body.new_password)
        return AuthPasswordChangeResponse(updated=True)

    return router
