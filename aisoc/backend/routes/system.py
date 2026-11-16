"""System routes for AISOC backend."""

from __future__ import annotations

from _thread import interrupt_main as _interrupt_main
import os

from fastapi import APIRouter, BackgroundTasks, Request, status

from aisoc.backend.auth import require_admin_user, require_authenticated_user
from aisoc.backend.config import AisocSettings
from aisoc.backend.models import (
    HealthResponse,
    SystemBootstrapResponse,
    SystemRestartResponse,
)
from aisoc.backend.services.user_service import UserService
from hermes_self_restart import request_self_restart


def _graceful_shutdown() -> None:
    """Ask the serving process to stop after the response has been sent."""
    _interrupt_main()


def build_system_router(
    settings: AisocSettings,
    user_service: UserService,
    *,
    admin_setup_required: bool,
) -> APIRouter:
    router = APIRouter(tags=["system"])

    @router.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", pid=os.getpid())

    @router.get("/api/system/bootstrap", response_model=SystemBootstrapResponse)
    async def bootstrap() -> SystemBootstrapResponse:
        return SystemBootstrapResponse(
            auth_scheme="jwt-password",
            admin_setup_required=admin_setup_required,
        )

    @router.post(
        "/api/system/restart",
        response_model=SystemRestartResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def restart(
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> SystemRestartResponse:
        user, _payload = require_authenticated_user(request, settings, user_service)
        require_admin_user(user)
        result = request_self_restart(
            "aisoc",
            lambda: background_tasks.add_task(_graceful_shutdown),
        )
        return SystemRestartResponse(**result.as_dict())

    return router
