"""Authenticated Portal-backed application entry routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from aegis.backend.auth import require_authenticated_user
from aegis.backend.config import AegisSettings
from aegis.backend.models import AppEntryListResponse
from aegis.backend.services.app_entry_service import AppEntryService
from aegis.backend.services.user_service import UserService


def build_app_entries_router(
    settings: AegisSettings,
    user_service: UserService,
    service: AppEntryService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/app-entries", tags=["app-entries"])
    app_entry_service = service or AppEntryService(settings)

    @router.get("", response_model=AppEntryListResponse)
    async def list_app_entries(request: Request) -> AppEntryListResponse:
        require_authenticated_user(request, settings, user_service)
        return await app_entry_service.list_entries()

    return router
