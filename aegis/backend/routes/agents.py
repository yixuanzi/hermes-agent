"""Agent orchestration routes for Aegis backend."""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from aegis.backend.auth import require_admin_user, require_authenticated_user
from aegis.backend.config import AegisSettings
from aegis.backend.models import (
    AgentDeleteResponse,
    AgentListResponse,
    AgentResponse,
    AgentUpsertRequest,
)
from aegis.backend.services.agent_service import AgentService
from aegis.backend.services.chat_quick_command_service import ChatQuickCommandService
from aegis.backend.services.user_service import UserService


def build_agents_router(
    settings: AegisSettings,
    user_service: UserService,
    service: AgentService | None = None,
    quick_command_service: ChatQuickCommandService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/agents", tags=["agents"])

    def _service() -> AgentService:
        return service or AgentService()

    def _ensure_admin(request: Request) -> None:
        user, _payload = require_authenticated_user(request, settings, user_service)
        require_admin_user(user)

    def _invalidate_quick_commands() -> None:
        if quick_command_service is not None:
            quick_command_service.invalidate_all()

    @router.get("", response_model=AgentListResponse)
    async def list_agents(request: Request) -> AgentListResponse:
        _ensure_admin(request)
        return AgentListResponse(agents=_service().list_agents())

    @router.get("/{agent_id}", response_model=AgentResponse)
    async def get_agent(agent_id: str, request: Request) -> AgentResponse:
        _ensure_admin(request)
        return _service().get_agent(agent_id)

    @router.post(
        "/{agent_id}",
        response_model=AgentResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_agent(agent_id: str, body: AgentUpsertRequest, request: Request) -> AgentResponse:
        _ensure_admin(request)
        response = _service().create_agent(agent_id, body)
        _invalidate_quick_commands()
        return response

    @router.put("/{agent_id}", response_model=AgentResponse)
    async def update_agent(agent_id: str, body: AgentUpsertRequest, request: Request) -> AgentResponse:
        _ensure_admin(request)
        response = _service().update_agent(agent_id, body)
        _invalidate_quick_commands()
        return response

    @router.delete("/{agent_id}", response_model=AgentDeleteResponse)
    async def delete_agent(agent_id: str, request: Request) -> AgentDeleteResponse:
        _ensure_admin(request)
        _service().delete_agent(agent_id)
        _invalidate_quick_commands()
        return AgentDeleteResponse(deleted=True, agent_id=agent_id)

    return router
