"""WebSocket chat routes for the Aegis backend."""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, WebSocket, status

from aegis.backend.auth import get_current_user_from_token, require_authenticated_user
from aegis.backend.chat.connection import ChatConnection
from aegis.backend.chat.drawer import drawer_file_response
from aegis.backend.chat.service import ChatSessionManager
from aegis.backend.config import AegisSettings
from aegis.backend.models import ChatQuickCommandListResponse, DrawerFileResponse
from aegis.backend.services.agent_service import AgentService
from aegis.backend.services.chat_quick_command_service import ChatQuickCommandService
from aegis.backend.services.prompt_template_service import PromptTemplateService
from aegis.backend.services.prompt_template_store import PromptTemplateStore
from aegis.backend.services.system_instruct_service import SystemInstructService
from aegis.backend.services.system_instruct_store import SystemInstructStore
from aegis.backend.services.user_service import UserService


def _ws_token(websocket: WebSocket) -> str | None:
    token = (websocket.query_params.get("token") or "").strip()
    return token or None


def build_chat_router(
    settings: AegisSettings,
    user_service: UserService,
    manager: ChatSessionManager | None = None,
    agent_service: AgentService | None = None,
    prompt_template_service: PromptTemplateService | None = None,
    system_instruct_service: SystemInstructService | None = None,
    quick_command_service: ChatQuickCommandService | None = None,
) -> APIRouter:
    router = APIRouter(tags=["chat"])
    session_manager = manager or ChatSessionManager()
    resolved_agent_service = agent_service or AgentService()
    resolved_prompt_template_service = prompt_template_service or PromptTemplateService(
        PromptTemplateStore()
    )
    resolved_system_instruct_service = system_instruct_service or SystemInstructService(
        SystemInstructStore()
    )
    resolved_quick_command_service = quick_command_service or ChatQuickCommandService(
        resolved_agent_service,
        resolved_prompt_template_service,
        resolved_system_instruct_service,
    )

    @router.post("/api/chat/attachments", status_code=status.HTTP_201_CREATED)
    async def upload_attachment(request: Request, file: UploadFile = File(...)) -> dict:
        user, _payload = require_authenticated_user(request, settings, user_service)
        attachment = await session_manager.attachments.upload(file, owner_id=user.uid)
        return {"attachment": attachment.public_dict()}

    @router.get("/api/chat/quick-commands", response_model=ChatQuickCommandListResponse)
    async def list_quick_commands(request: Request) -> ChatQuickCommandListResponse:
        """Return the authenticated user's available composer shortcuts."""
        user, _payload = require_authenticated_user(request, settings, user_service)
        return ChatQuickCommandListResponse(
            commands=resolved_quick_command_service.list_commands(user.uid)
        )

    @router.get("/api/chat/drawer-html", response_model=DrawerFileResponse)
    def get_drawer_html(path: str, request: Request) -> DrawerFileResponse:
        """Return a typed preview payload for one file this user's agent wrote."""
        user, _payload = require_authenticated_user(request, settings, user_service)
        return drawer_file_response(path, user_id=user.uid, ledger=session_manager.drawer_files)

    @router.websocket("/api/chat/ws")
    async def chat_ws(websocket: WebSocket) -> None:
        token = _ws_token(websocket)
        if not token:
            await websocket.close(code=4401)
            return
        try:
            current_user, _payload = get_current_user_from_token(token, settings, user_service)
        except HTTPException:
            await websocket.close(code=4401)
            return

        await websocket.accept()
        await ChatConnection(
            websocket,
            current_user,
            session_manager,
            resolved_quick_command_service,
        ).serve()

    return router
