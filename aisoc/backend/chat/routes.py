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
import base64
import mimetypes
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, WebSocket, status
from starlette.websockets import WebSocketDisconnect

from aisoc.backend.auth import get_current_user_from_token, require_authenticated_user
from aisoc.backend.chat.service import ChatSessionManager
from aisoc.backend.config import AisocSettings
from aisoc.backend.models import ChatQuickCommandListResponse, DrawerFileResponse
from aisoc.backend.services.quick_command_service import (
    MessageArgumentResolutionError,
    QuickCommandService,
)
from aisoc.backend.services.user_service import UserService


ATTACHMENT_OWNER_ID = "aisoc"

_IMAGE_SUFFIXES = frozenset({".avif", ".bmp", ".gif", ".ico", ".jpeg", ".jpg", ".png", ".svg", ".webp"})
_DRAWER_FILE_TYPES = {
    ".css": "css",
    ".csv": "text",
    ".html": "html",
    ".htm": "html",
    ".java": "java",
    ".js": "javascript",
    ".json": "json",
    ".jsx": "javascript",
    ".md": "markdown",
    ".mdx": "markdown",
    ".markdown": "markdown",
    ".py": "python",
    ".rs": "rust",
    ".sh": "shell",
    ".sql": "sql",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".txt": "text",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
}


def _workspace_root() -> Path:
    """Resolve the only filesystem root exposed by the drawer preview API."""
    return Path.cwd().resolve()


def _resolve_workspace_drawer_file(path: str) -> tuple[Path, Path]:
    root = _workspace_root()
    requested = Path(str(path or "").strip())
    if not str(requested) or requested == Path("."):
        raise HTTPException(status_code=404, detail="Drawer file not found.")
    candidate = requested if requested.is_absolute() else root / requested
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Drawer path is outside the workspace.") from exc
    except OSError as exc:
        raise HTTPException(status_code=404, detail="Drawer file not found.") from exc
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="Drawer file not found.")
    return root, resolved


def _drawer_file_type(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix in _IMAGE_SUFFIXES:
        return suffix[1:]
    return _DRAWER_FILE_TYPES.get(suffix, "text")


def _drawer_file_content(file_path: Path) -> str | None:
    if file_path.suffix.lower() in _IMAGE_SUFFIXES:
        media_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
        return f"data:{media_type};base64,{encoded}"
    try:
        return file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def _drawer_file_response(path: str) -> DrawerFileResponse:
    root, file_path = _resolve_workspace_drawer_file(path)
    file_type = _drawer_file_type(file_path)
    content = _drawer_file_content(file_path)
    if content is None:
        file_type = "binary"
    return DrawerFileResponse(
        title=str(file_path.relative_to(root)),
        type=file_type,
        content=content or "",
    )


def _ws_token(websocket: WebSocket) -> str | None:
    token = (websocket.query_params.get("token") or "").strip()
    return token or None


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
        """Return a typed preview payload for one file in the Hermes workspace."""
        require_authenticated_user(request, settings, user_service)
        return _drawer_file_response(path)

    @router.websocket("/api/chat/session")
    async def chat_session_ws(websocket: WebSocket) -> None:
        token = _ws_token(websocket)
        if not token:
            await websocket.close(code=4401)
            return
        current_user = None
        try:
            current_user, _payload = get_current_user_from_token(token, settings, user_service)
        except Exception:
            await websocket.close(code=4401)
            return

        await websocket.accept()
        actor = None
        try:
            while True:
                payload = await websocket.receive_json()
                if not isinstance(payload, dict):
                    await websocket.send_json(
                        {
                            "type": "error",
                            "code": "invalid_payload",
                            "message": "Payload must be a JSON object.",
                        }
                    )
                    continue

                event_type = str(payload.get("type") or "").strip()
                if event_type == "session.bind":
                    actor = session_manager.bind(
                        websocket,
                        asyncio.get_running_loop(),
                        session_id=str(payload.get("session_id") or "").strip() or None,
                        title=str(payload.get("title") or "").strip() or None,
                        user_id=current_user.uid if current_user is not None else None,
                        user_name=current_user.username if current_user is not None else None,
                    )
                    await websocket.send_json(
                        actor.build_bound_event(resumed=bool(payload.get("session_id")))
                    )
                    continue

                if actor is None:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "code": "session_not_bound",
                            "message": "Bind a session before sending chat events.",
                        }
                    )
                    continue

                if event_type == "message.send":
                    try:
                        resolved_text = resolved_quick_command_service.resolve_text(
                            str(payload.get("text") or ""),
                            args=payload.get("args"),
                        )
                        attachments = session_manager.attachments.resolve(
                            payload.get("attachments"),
                            owner_id=current_user.uid if current_user is not None else ATTACHMENT_OWNER_ID,
                        )
                        actor.handle_message(
                            resolved_text,
                            client_msg_id=str(payload.get("client_msg_id") or "").strip() or None,
                            attachments=attachments,
                        )
                    except MessageArgumentResolutionError as exc:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "code": "invalid_message_args",
                                "message": str(exc),
                                "client_msg_id": str(payload.get("client_msg_id") or "").strip() or None,
                            }
                        )
                    except ValueError as exc:
                        await websocket.send_json(
                            {"type": "error", "code": "invalid_attachment", "message": str(exc)}
                        )
                    continue

                if event_type == "approval.respond":
                    actor.handle_approval_response(str(payload.get("choice") or ""))
                    continue

                if event_type == "clarify.respond":
                    actor.handle_clarify_response(str(payload.get("answer") or ""))
                    continue

                if event_type == "session.interrupt":
                    actor.interrupt()
                    continue

                if event_type == "session.resume":
                    for event in actor.resume_state_events():
                        await websocket.send_json(event)
                    continue

                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "unsupported_event",
                        "message": f"Unsupported websocket event: {event_type or '<empty>'}",
                    }
                )
        except WebSocketDisconnect:
            if actor is not None:
                actor.detach_connection(websocket)
    return router
