"""WebSocket + REST chat routes for the AISOC backend.

Forked from ``aegis/backend/chat/routes.py``. Differences:
- user identity is carried by a per-user JWT (query ``?token=`` on the
  WebSocket; HTTP endpoints are covered by the global JWT middleware in
  ``server.py``); sessions bind with the authenticated user's ``user_id``
- quick commands are global (not per-user scoped) in AISOC, unlike Aegis
- WebSocket path is ``/api/chat/session`` (the legacy tui_gateway bridge owns
  ``/api/chat/ws`` until it is removed)
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, WebSocket, status
from starlette.types import Message
from starlette.websockets import WebSocketDisconnect

from aisoc.backend.auth import get_current_user_from_token, require_authenticated_user
from aisoc.backend.chat.drawer import drawer_file_response
from aisoc.backend.chat.service import ChatSessionActor, ChatSessionManager
from aisoc.backend.config import AisocSettings
from aisoc.backend.models import ChatQuickCommandListResponse, DrawerFileResponse, UserResponse
from aisoc.backend.services.quick_command_service import (
    MessageArgumentResolutionError,
    QuickCommandService,
)
from aisoc.backend.services.user_service import UserService


def _ws_token(websocket: WebSocket) -> str | None:
    token = (websocket.query_params.get("token") or "").strip()
    return token or None


def _decode_frame(message: Message) -> object:
    """JSON value of one text frame; ``ValueError`` for binary or non-JSON frames."""
    text = message.get("text")
    if text is None:
        raise ValueError("binary frame")
    return json.loads(text)


def _client_msg_id(payload: dict) -> str | None:
    return str(payload.get("client_msg_id") or "").strip() or None


class _ChatSocket:
    """One authenticated ``/api/chat/session`` connection.

    Events are handled one at a time, in arrival order. Work that can block
    (building a cold agent, running a turn's setup, cancelling a remote
    delegate) runs in a worker thread so the event loop keeps serving every
    other request; the actor's ``_send_event`` hops back onto the loop with
    ``run_coroutine_threadsafe``, so it is safe from those threads.
    """

    def __init__(
        self,
        websocket: WebSocket,
        user: UserResponse,
        manager: ChatSessionManager,
        quick_commands: QuickCommandService,
    ) -> None:
        self._websocket = websocket
        self._user = user
        self._manager = manager
        self._quick_commands = quick_commands
        self._actor: ChatSessionActor | None = None
        self._bound_actors: list[ChatSessionActor] = []
        self._handlers = {
            "message.send": self._send_message,
            "approval.respond": self._respond_approval,
            "clarify.respond": self._respond_clarify,
            "session.interrupt": self._interrupt,
            "session.resume": self._resume,
        }

    async def serve(self) -> None:
        try:
            while True:
                message = await self._websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                await self._dispatch(message)
        except WebSocketDisconnect:
            return
        finally:
            # Every exit, an unexpected exception included, releases the
            # actors this socket bound; otherwise they keep a dead socket as
            # their event sink and the session never learns its client left.
            for actor in self._bound_actors:
                actor.detach_connection(self._websocket)

    async def _error(self, code: str, message: str, **extra: object) -> None:
        await self._websocket.send_json({"type": "error", "code": code, "message": message, **extra})

    async def _dispatch(self, message: Message) -> None:
        try:
            payload = _decode_frame(message)
        except ValueError:
            await self._error("invalid_message", "Frames must be JSON text.")
            return
        if not isinstance(payload, dict):
            await self._error("invalid_payload", "Payload must be a JSON object.")
            return

        event_type = str(payload.get("type") or "").strip()
        if event_type == "session.bind":
            await self._bind(payload)
            return
        if self._actor is None:
            await self._error("session_not_bound", "Bind a session before sending chat events.")
            return
        handler = self._handlers.get(event_type)
        if handler is None:
            await self._error(
                "unsupported_event", f"Unsupported websocket event: {event_type or '<empty>'}"
            )
            return
        await handler(self._actor, payload)

    async def _bind(self, payload: dict) -> None:
        actor = await asyncio.to_thread(
            self._manager.bind,
            self._websocket,
            asyncio.get_running_loop(),
            session_id=str(payload.get("session_id") or "").strip() or None,
            title=str(payload.get("title") or "").strip() or None,
            user_id=self._user.uid,
            user_name=self._user.username,
        )
        self._actor = actor
        if actor not in self._bound_actors:
            self._bound_actors.append(actor)
        await self._websocket.send_json(
            actor.build_bound_event(resumed=bool(payload.get("session_id")))
        )

    async def _send_message(self, actor: ChatSessionActor, payload: dict) -> None:
        client_msg_id = _client_msg_id(payload)
        try:
            text = self._quick_commands.resolve_text(
                str(payload.get("text") or ""), args=payload.get("args")
            )
            attachments = self._manager.attachments.resolve(
                payload.get("attachments"), owner_id=self._user.uid
            )
        except MessageArgumentResolutionError as exc:
            await self._error("invalid_message_args", str(exc), client_msg_id=client_msg_id)
            return
        except ValueError as exc:
            await self._error("invalid_attachment", str(exc), client_msg_id=client_msg_id)
            return
        except HTTPException as exc:
            await self._error("message_rejected", str(exc.detail), client_msg_id=client_msg_id)
            return
        await asyncio.to_thread(
            actor.handle_message, text, client_msg_id=client_msg_id, attachments=attachments
        )

    async def _respond_approval(self, actor: ChatSessionActor, payload: dict) -> None:
        actor.handle_approval_response(str(payload.get("choice") or ""))

    async def _respond_clarify(self, actor: ChatSessionActor, payload: dict) -> None:
        actor.handle_clarify_response(str(payload.get("answer") or ""))

    async def _interrupt(self, actor: ChatSessionActor, payload: dict) -> None:
        # Cancelling a remote A2A delegate is a network call.
        await asyncio.to_thread(actor.interrupt)

    async def _resume(self, actor: ChatSessionActor, payload: dict) -> None:
        for event in actor.resume_state_events():
            await self._websocket.send_json(event)


def build_agent_chat_router(
    settings: AisocSettings,
    user_service: UserService,
    manager: ChatSessionManager | None = None,
    quick_command_service: QuickCommandService | None = None,
) -> APIRouter:
    router = APIRouter(tags=["agent-chat"])
    session_manager = manager or ChatSessionManager()
    resolved_quick_command_service = quick_command_service or QuickCommandService()

    @router.post("/api/chat/attachments", status_code=status.HTTP_201_CREATED)
    async def upload_attachment(request: Request, file: UploadFile = File(...)) -> dict:
        user, _payload = require_authenticated_user(request, settings, user_service)
        attachment = await session_manager.attachments.upload(file, owner_id=user.uid)
        return {"attachment": attachment.public_dict()}

    @router.get("/api/chat/quick-commands", response_model=ChatQuickCommandListResponse)
    async def list_quick_commands(request: Request) -> ChatQuickCommandListResponse:
        """Return the available composer shortcuts."""
        require_authenticated_user(request, settings, user_service)
        return ChatQuickCommandListResponse(
            commands=resolved_quick_command_service.list_commands()
        )

    @router.get("/api/chat/drawer-html", response_model=DrawerFileResponse)
    async def get_drawer_html(path: str, request: Request) -> DrawerFileResponse:
        """Return a typed preview payload for one file this user's agent wrote."""
        user, _payload = require_authenticated_user(request, settings, user_service)
        return drawer_file_response(path, user_id=user.uid, ledger=session_manager.drawer_files)

    @router.websocket("/api/chat/session")
    async def chat_session_ws(websocket: WebSocket) -> None:
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
        await _ChatSocket(
            websocket, current_user, session_manager, resolved_quick_command_service
        ).serve()

    return router
