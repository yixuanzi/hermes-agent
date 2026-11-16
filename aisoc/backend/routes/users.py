"""User management routes for AISOC backend."""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from aisoc.backend.auth import require_admin_user, require_authenticated_user
from aisoc.backend.config import AisocSettings
from aisoc.backend.models import (
    UserCreateRequest,
    UserDeleteResponse,
    UserListResponse,
    UserPasswordUpdateRequest,
    UserPasswordUpdateResponse,
    UserResponse,
    UserStatusUpdateRequest,
)
from aisoc.backend.services.user_service import UserService


def build_users_router(settings: AisocSettings, user_service: UserService) -> APIRouter:
    router = APIRouter(prefix="/api/users", tags=["users"])

    def _ensure_admin(request: Request) -> None:
        user, _payload = require_authenticated_user(request, settings, user_service)
        require_admin_user(user)

    @router.get("", response_model=UserListResponse)
    async def list_users(request: Request) -> UserListResponse:
        _ensure_admin(request)
        return UserListResponse(users=user_service.list_users())

    @router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
    async def create_user(body: UserCreateRequest, request: Request) -> UserResponse:
        _ensure_admin(request)
        return user_service.create_user(body)

    @router.put("/{uid}/status", response_model=UserResponse)
    async def update_status(
        uid: str,
        body: UserStatusUpdateRequest,
        request: Request,
    ) -> UserResponse:
        _ensure_admin(request)
        return user_service.update_status(uid, body.status)

    @router.put("/{uid}/password", response_model=UserPasswordUpdateResponse)
    async def update_password(
        uid: str,
        body: UserPasswordUpdateRequest,
        request: Request,
    ) -> UserPasswordUpdateResponse:
        _ensure_admin(request)
        user_service.reset_password(uid, body.password)
        return UserPasswordUpdateResponse(updated=True, uid=uid)

    @router.delete("/{uid}", response_model=UserDeleteResponse)
    async def delete_user(uid: str, request: Request) -> UserDeleteResponse:
        _ensure_admin(request)
        user_service.delete_user(uid)
        return UserDeleteResponse(deleted=True, uid=uid)

    return router
