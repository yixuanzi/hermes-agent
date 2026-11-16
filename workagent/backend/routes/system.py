"""System routes for WORKAGENT backend."""

from __future__ import annotations

from _thread import interrupt_main as _interrupt_main
import os

from fastapi import APIRouter, BackgroundTasks, status

from workagent.backend.config import WorkagentSettings
from workagent.backend.models import (
    HealthResponse,
    SystemBootstrapResponse,
    SystemRestartResponse,
)
from hermes_self_restart import request_self_restart


def _graceful_shutdown() -> None:
    """Ask the serving process to stop after the response has been sent."""
    _interrupt_main()


def build_system_router(settings: WorkagentSettings) -> APIRouter:
    router = APIRouter(tags=["system"])

    @router.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", pid=os.getpid())

    @router.get("/api/system/bootstrap", response_model=SystemBootstrapResponse)
    async def bootstrap() -> SystemBootstrapResponse:
        return SystemBootstrapResponse(
            embedded_chat=settings.embedded_chat,
            auth_scheme="bearer-token",
        )

    @router.post(
        "/api/system/restart",
        response_model=SystemRestartResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def restart(background_tasks: BackgroundTasks) -> SystemRestartResponse:
        result = request_self_restart(
            "workagent",
            lambda: background_tasks.add_task(_graceful_shutdown),
        )
        return SystemRestartResponse(**result.as_dict())

    return router
