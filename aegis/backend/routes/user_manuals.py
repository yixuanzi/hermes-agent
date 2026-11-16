"""Authenticated routes for reading bundled Aegis user manuals."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from aegis.backend.auth import require_authenticated_user
from aegis.backend.config import AegisSettings
from aegis.backend.models import UserManualListResponse, UserManualResponse
from aegis.backend.services.user_manual_service import UserManualService


def build_user_manuals_router(
    settings: AegisSettings,
    user_service,
    user_manual_service: UserManualService,
) -> APIRouter:
    router = APIRouter(prefix="/api/user-manuals", tags=["user-manuals"])

    def _require_user(request: Request) -> None:
        require_authenticated_user(request, settings, user_service)

    @router.get("", response_model=UserManualListResponse)
    async def list_manuals(request: Request) -> UserManualListResponse:
        _require_user(request)
        return UserManualListResponse(
            manuals=user_manual_service.list_manuals(),
            default_manual_id=user_manual_service.default_manual_id(),
        )

    @router.get("/{manual_id}", response_model=UserManualResponse)
    async def get_manual(manual_id: str, request: Request) -> UserManualResponse:
        _require_user(request)
        manual = user_manual_service.get_manual(manual_id)
        if manual is None:
            raise HTTPException(status_code=404, detail="User manual not found.")
        return manual

    return router
