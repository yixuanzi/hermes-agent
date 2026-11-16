"""Administrator CRUD routes for global Aegis system instructions."""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from aegis.backend.auth import require_admin_user, require_authenticated_user
from aegis.backend.config import AegisSettings
from aegis.backend.models import (
    SystemInstructDeleteResponse,
    SystemInstructListResponse,
    SystemInstructRequest,
    SystemInstructResponse,
)
from aegis.backend.services.system_instruct_service import SystemInstructService
from aegis.backend.services.chat_quick_command_service import ChatQuickCommandService
from aegis.backend.services.user_service import UserService


def build_system_instructs_router(
    settings: AegisSettings,
    user_service: UserService,
    system_instruct_service: SystemInstructService,
    quick_command_service: ChatQuickCommandService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/system-instructs", tags=["system-instructs"])

    def _ensure_admin(request: Request) -> None:
        user, _payload = require_authenticated_user(request, settings, user_service)
        require_admin_user(user)

    @router.get("", response_model=SystemInstructListResponse)
    async def list_instructions(request: Request) -> SystemInstructListResponse:
        _ensure_admin(request)
        return SystemInstructListResponse(
            instructions=system_instruct_service.list_instructions()
        )

    @router.post("", response_model=SystemInstructResponse, status_code=status.HTTP_201_CREATED)
    async def create_instruction(
        body: SystemInstructRequest,
        request: Request,
    ) -> SystemInstructResponse:
        _ensure_admin(request)
        response = system_instruct_service.create_instruction(body)
        if quick_command_service is not None:
            quick_command_service.invalidate_all()
        return response

    @router.put("/{instruct_id}", response_model=SystemInstructResponse)
    async def update_instruction(
        instruct_id: str,
        body: SystemInstructRequest,
        request: Request,
    ) -> SystemInstructResponse:
        _ensure_admin(request)
        response = system_instruct_service.update_instruction(instruct_id, body)
        if quick_command_service is not None:
            quick_command_service.invalidate_all()
        return response

    @router.delete("/{instruct_id}", response_model=SystemInstructDeleteResponse)
    async def delete_instruction(
        instruct_id: str,
        request: Request,
    ) -> SystemInstructDeleteResponse:
        _ensure_admin(request)
        system_instruct_service.delete_instruction(instruct_id)
        if quick_command_service is not None:
            quick_command_service.invalidate_all()
        return SystemInstructDeleteResponse(deleted=True, id=instruct_id)

    return router
