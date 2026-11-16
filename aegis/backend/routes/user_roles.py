"""Administrator CRUD routes for user role records."""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from aegis.backend.auth import require_admin_user, require_authenticated_user
from aegis.backend.config import AegisSettings
from aegis.backend.models import (
    UserRoleDeleteResponse,
    UserRoleListResponse,
    UserRoleRequest,
    UserRoleResponse,
)
from aegis.backend.services.user_role_service import UserRoleService
from aegis.backend.services.user_service import UserService


def build_user_roles_router(
    settings: AegisSettings,
    user_service: UserService,
    user_role_service: UserRoleService,
) -> APIRouter:
    router = APIRouter(prefix="/api/user-roles", tags=["user-roles"])

    def _ensure_admin(request: Request) -> None:
        user, _payload = require_authenticated_user(request, settings, user_service)
        require_admin_user(user)

    @router.get("", response_model=UserRoleListResponse)
    async def list_user_roles(request: Request) -> UserRoleListResponse:
        _ensure_admin(request)
        return UserRoleListResponse(roles=user_role_service.list_roles())

    @router.post("", response_model=UserRoleResponse, status_code=status.HTTP_201_CREATED)
    async def create_user_role(
        body: UserRoleRequest,
        request: Request,
    ) -> UserRoleResponse:
        _ensure_admin(request)
        return user_role_service.create_role(body)

    @router.put("/{role_id}", response_model=UserRoleResponse)
    async def update_user_role(
        role_id: str,
        body: UserRoleRequest,
        request: Request,
    ) -> UserRoleResponse:
        _ensure_admin(request)
        return user_role_service.update_role(role_id, body)

    @router.delete("/{role_id}", response_model=UserRoleDeleteResponse)
    async def delete_user_role(
        role_id: str,
        request: Request,
    ) -> UserRoleDeleteResponse:
        _ensure_admin(request)
        user_role_service.delete_role(role_id)
        return UserRoleDeleteResponse(deleted=True, id=role_id)

    return router
