"""One authenticated chat websocket: decode frames, bind a session, dispatch its events in order."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
from typing import Any

from fastapi import HTTPException, WebSocket
from starlette.types import Message
from starlette.websockets import WebSocketDisconnect

from aegis.backend.chat.service import ChatSessionActor, ChatSessionManager
from aegis.backend.models import UserResponse
from aegis.backend.services.chat_quick_command_service import (
    ChatQuickCommandService,
    MessageArgumentResolutionError,
)


_INVALID_MESSAGE = "Each websocket frame must be a JSON object."


def _error_event(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"type": "error", "code": code, "message": message, **extra}


def _decode_frame(message: Message) -> dict[str, Any] | None:
    """The frame's JSON object, or None for a binary, non-JSON or non-object frame."""
    text = message.get("text")
    if text is None:
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


class ChatConnection:
    """Drives one socket for one user.

    Actor calls that can block run in a worker thread: building an agent probes every A2A agent
    card, ``/model`` may fetch a model catalog and delegate responders reach remote agents, and on
    the event loop any of them stalls every other request. Frames are still handled one at a time,
    so per-socket ordering holds; actor events reach the socket through ``_send_event``, which
    hops back to the loop with ``run_coroutine_threadsafe``.
    """

    def __init__(
        self,
        websocket: WebSocket,
        user: UserResponse,
        manager: ChatSessionManager,
        quick_commands: ChatQuickCommandService,
    ) -> None:
        self._websocket = websocket
        self._user = user
        self._manager = manager
        self._quick_commands = quick_commands
        self._loop = asyncio.get_running_loop()
        self._actor: ChatSessionActor | None = None

    async def serve(self) -> None:
        try:
            while True:
                message = await self._websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                payload = _decode_frame(message)
                if payload is None:
                    await self._websocket.send_json(_error_event("invalid_message", _INVALID_MESSAGE))
                    continue
                await self._handle(payload)
        except WebSocketDisconnect:
            pass  # The client left mid-send; the finally below detaches it.
        finally:
            # On every exit, or the actor keeps a dead socket and stays pinned against eviction.
            if self._actor is not None:
                self._manager.release_connection(self._actor, self._websocket)

    async def _handle(self, payload: dict[str, Any]) -> None:
        event_type = str(payload.get("type") or "").strip()
        if event_type == "session.bind":
            await self._bind(payload)
            return
        if self._actor is None:
            await self._websocket.send_json(
                _error_event("session_not_bound", "Bind a session before sending chat events.")
            )
            return
        handler = _EVENT_HANDLERS.get(event_type)
        if handler is None:
            await self._websocket.send_json(
                _error_event("unsupported_event", f"Unsupported websocket event: {event_type or '<empty>'}")
            )
            return
        await handler(self, self._actor, payload)

    async def _bind(self, payload: dict[str, Any]) -> None:
        def _bind_session() -> tuple[ChatSessionActor, bool]:
            actor = self._manager.bind(
                self._websocket,
                self._loop,
                session_id=str(payload.get("session_id") or "").strip() or None,
                title=str(payload.get("title") or "").strip() or None,
                user_id=self._user.uid,
                user_name=self._user.username,
            )
            return actor, actor.has_prior_state()

        self._actor, resumed = await asyncio.to_thread(_bind_session)
        await self._websocket.send_json(self._actor.build_bound_event(resumed=resumed))

    async def _send_message(self, actor: ChatSessionActor, payload: dict[str, Any]) -> None:
        error = await asyncio.to_thread(self._run_message, actor, payload)
        if error is not None:
            await self._websocket.send_json(error)

    def _run_message(self, actor: ChatSessionActor, payload: dict[str, Any]) -> dict[str, Any] | None:
        client_msg_id = str(payload.get("client_msg_id") or "").strip() or None
        try:
            text = self._quick_commands.resolve_text(
                self._user.uid,
                str(payload.get("text") or ""),
                args=payload.get("args"),
            )
            attachments = self._manager.attachments.resolve(
                payload.get("attachments"),
                owner_id=self._user.uid,
            )
            actor.handle_message(text, client_msg_id=client_msg_id, attachments=attachments)
        except MessageArgumentResolutionError as exc:
            return _error_event("invalid_message_args", str(exc), client_msg_id=client_msg_id)
        except ValueError as exc:
            return _error_event("invalid_attachment", str(exc))
        except HTTPException as exc:
            return _error_event("message_rejected", str(exc.detail), client_msg_id=client_msg_id)
        return None

    async def _respond_approval(self, actor: ChatSessionActor, payload: dict[str, Any]) -> None:
        await asyncio.to_thread(
            actor.handle_approval_response,
            str(payload.get("choice") or ""),
            str(payload.get("approval_id") or "") or None,
        )

    async def _respond_clarify(self, actor: ChatSessionActor, payload: dict[str, Any]) -> None:
        await asyncio.to_thread(
            actor.handle_clarify_response,
            str(payload.get("answer") or ""),
            str(payload.get("clarify_id") or "") or None,
        )

    async def _interrupt(self, actor: ChatSessionActor, _payload: dict[str, Any]) -> None:
        await asyncio.to_thread(actor.interrupt)

    async def _resume(self, actor: ChatSessionActor, _payload: dict[str, Any]) -> None:
        for event in actor.resume_state_events():
            await self._websocket.send_json(event)


_EVENT_HANDLERS: dict[
    str, Callable[[ChatConnection, ChatSessionActor, dict[str, Any]], Awaitable[None]]
] = {
    "message.send": ChatConnection._send_message,
    "approval.respond": ChatConnection._respond_approval,
    "clarify.respond": ChatConnection._respond_clarify,
    "session.interrupt": ChatConnection._interrupt,
    "session.resume": ChatConnection._resume,
}
