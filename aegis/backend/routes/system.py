"""System routes for Aegis backend."""

from __future__ import annotations

from _thread import interrupt_main as _interrupt_main
import os

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from starlette.concurrency import run_in_threadpool

from aegis.backend.auth import require_admin_user, require_authenticated_user
from aegis.backend.config import AegisSettings
from aegis.backend.main import build_web_ui_before_restart
from aegis.backend.models import HealthResponse, SystemBootstrapResponse, SystemRestartResponse
from aegis.backend.services.lark_service import lark_is_configured
from aegis.backend.services.oidc_service import oidc_is_configured
from aegis.backend.services.user_service import UserService
from aegis_common.self_restart import launch_environment, request_self_restart
from aegis_common.web_build import WebBuildError


# The tail of a failed build's output returned to the admin; the full output can be long.
_BUILD_OUTPUT_TAIL_CHARS = 4000


def _graceful_shutdown() -> None:
    """Ask the serving process to stop after the response has been sent."""
    _interrupt_main()


def build_system_router(
    settings: AegisSettings,
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
            embedded_chat=settings.embedded_chat,
            auth_scheme="jwt-password",
            admin_setup_required=admin_setup_required,
            # Booleans only: the login page hides an SSO entry whose start route would 503.
            oidc_sso_enabled=oidc_is_configured(settings),
            lark_sso_enabled=settings.lark_sso_enabled and lark_is_configured(settings),
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
        # Build before stopping: a replacement whose startup build fails exits with nothing
        # serving, while a failure here leaves this process up and says why.
        try:
            await run_in_threadpool(build_web_ui_before_restart, launch_environment())
        except WebBuildError as exc:
            output = exc.output[-_BUILD_OUTPUT_TAIL_CHARS:]
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Restart cancelled; Aegis is still running. {exc}"
                + (f"\n\n{output}" if output else ""),
            ) from exc
        result = request_self_restart(
            "aegis",
            lambda: background_tasks.add_task(_graceful_shutdown),
        )
        return SystemRestartResponse(**result.as_dict())

    return router
