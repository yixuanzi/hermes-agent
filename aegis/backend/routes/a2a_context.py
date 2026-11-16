"""Authenticated A2A context snapshot routes for the chat agent browser."""

from __future__ import annotations

from fastapi import APIRouter, Request

from aegis.backend.auth import require_authenticated_user
from aegis.backend.config import AegisSettings
from aegis.backend.models import A2AContextResponse
from aegis.backend.services.a2a_context_service import A2AContextService


def build_a2a_context_router(
    settings: AegisSettings,
    user_service,
    a2a_context_service: A2AContextService,
) -> APIRouter:
    router = APIRouter(prefix="/api/a2a/context", tags=["a2a-context"])

    def _require_user(request: Request) -> None:
        require_authenticated_user(request, settings, user_service)

    @router.get("", response_model=A2AContextResponse)
    async def get_context(request: Request) -> A2AContextResponse:
        _require_user(request)
        return a2a_context_service.get_context()

    @router.post("/refresh", response_model=A2AContextResponse)
    async def refresh_context(request: Request) -> A2AContextResponse:
        _require_user(request)
        return a2a_context_service.refresh_context()

    return router
