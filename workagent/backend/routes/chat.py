"""Chat routes for WORKAGENT backend."""

from __future__ import annotations

import asyncio
import hmac

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from workagent.backend.config import WorkagentSettings
from workagent.backend.services import tui_embed


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})
_PTY_READ_CHUNK_TIMEOUT = 0.2
_event_channels: dict[str, set[WebSocket]] = {}
_event_lock = asyncio.Lock()


def _ws_client_is_allowed(ws: WebSocket, settings: WorkagentSettings) -> bool:
    if settings.allow_public:
        return True
    host = ws.client.host if ws.client else ""
    if not host:
        return True
    return host in _LOOPBACK_HOSTS


def _valid_token(token: str, settings: WorkagentSettings) -> bool:
    return hmac.compare_digest(token.encode(), settings.session_token.encode())


async def _broadcast_event(channel: str, payload: str) -> None:
    async with _event_lock:
        subscribers = list(_event_channels.get(channel, ()))

    for subscriber in subscribers:
        try:
            await subscriber.send_text(payload)
        except Exception:
            pass


def build_chat_router(settings: WorkagentSettings) -> APIRouter:
    router = APIRouter(prefix="/api/chat", tags=["chat"])

    @router.get("/status")
    async def chat_status():
        return {"embedded_chat": settings.embedded_chat, "ready": settings.embedded_chat}

    @router.websocket("/pty")
    async def pty_ws(ws: WebSocket) -> None:
        if not settings.embedded_chat:
            await ws.close(code=4403)
            return

        token = ws.query_params.get("token", "")
        if not _valid_token(token, settings):
            await ws.close(code=4401)
            return
        if not _ws_client_is_allowed(ws, settings):
            await ws.close(code=4403)
            return

        await ws.accept()

        if not tui_embed.pty_bridge_available():
            await ws.send_text(
                "\r\n\x1b[31mChat unavailable: this platform cannot host a POSIX PTY.\x1b[0m\r\n"
            )
            await ws.close(code=1011)
            return

        resume = ws.query_params.get("resume") or None
        channel = tui_embed.channel_or_none(ws.query_params.get("channel", ""))
        sidecar_url = (
            tui_embed.build_sidecar_url(
                host=settings.host,
                port=settings.port,
                token=settings.session_token,
                channel=channel,
            )
            if channel
            else None
        )
        try:
            argv, cwd, env = tui_embed.resolve_chat_argv(
                resume=resume,
                sidecar_url=sidecar_url,
            )
        except SystemExit as exc:
            await ws.send_text(f"\r\n\x1b[31mChat unavailable: {exc}\x1b[0m\r\n")
            await ws.close(code=1011)
            return

        try:
            bridge = tui_embed.PtyBridge.spawn(argv, cwd=cwd, env=env)  # type: ignore[union-attr]
        except tui_embed.PtyUnavailableError as exc:
            await ws.send_text(f"\r\n\x1b[31mChat unavailable: {exc}\x1b[0m\r\n")
            await ws.close(code=1011)
            return
        except (FileNotFoundError, OSError) as exc:
            await ws.send_text(f"\r\n\x1b[31mChat failed to start: {exc}\x1b[0m\r\n")
            await ws.close(code=1011)
            return

        loop = asyncio.get_running_loop()

        async def pump_pty_to_ws() -> None:
            while True:
                chunk = await loop.run_in_executor(
                    None, bridge.read, _PTY_READ_CHUNK_TIMEOUT
                )
                if chunk is None:
                    return
                if not chunk:
                    await asyncio.sleep(0)
                    continue
                try:
                    await ws.send_bytes(chunk)
                except Exception:
                    return

        reader_task = asyncio.create_task(pump_pty_to_ws())
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                raw = msg.get("bytes")
                if raw is None:
                    text = msg.get("text")
                    raw = text.encode("utf-8") if isinstance(text, str) else b""
                if not raw:
                    continue
                resize = tui_embed.parse_resize_escape(raw)
                if resize is not None:
                    cols, rows = resize
                    bridge.resize(cols=cols, rows=rows)
                    continue
                bridge.write(raw)
        except WebSocketDisconnect:
            pass
        finally:
            reader_task.cancel()
            try:
                await reader_task
            except (asyncio.CancelledError, Exception):
                pass
            bridge.close()

    @router.websocket("/ws")
    async def gateway_ws(ws: WebSocket) -> None:
        if not settings.embedded_chat:
            await ws.close(code=4403)
            return
        token = ws.query_params.get("token", "")
        if not _valid_token(token, settings):
            await ws.close(code=4401)
            return
        if not _ws_client_is_allowed(ws, settings):
            await ws.close(code=4403)
            return
        from tui_gateway.ws import handle_ws

        await handle_ws(ws)

    @router.websocket("/pub")
    async def pub_ws(ws: WebSocket) -> None:
        if not settings.embedded_chat:
            await ws.close(code=4403)
            return
        token = ws.query_params.get("token", "")
        if not _valid_token(token, settings):
            await ws.close(code=4401)
            return
        if not _ws_client_is_allowed(ws, settings):
            await ws.close(code=4403)
            return
        channel = tui_embed.channel_or_none(ws.query_params.get("channel", ""))
        if not channel:
            await ws.close(code=4400)
            return
        await ws.accept()
        try:
            while True:
                await _broadcast_event(channel, await ws.receive_text())
        except WebSocketDisconnect:
            pass

    @router.websocket("/events")
    async def events_ws(ws: WebSocket) -> None:
        if not settings.embedded_chat:
            await ws.close(code=4403)
            return
        token = ws.query_params.get("token", "")
        if not _valid_token(token, settings):
            await ws.close(code=4401)
            return
        if not _ws_client_is_allowed(ws, settings):
            await ws.close(code=4403)
            return
        channel = tui_embed.channel_or_none(ws.query_params.get("channel", ""))
        if not channel:
            await ws.close(code=4400)
            return
        await ws.accept()
        async with _event_lock:
            _event_channels.setdefault(channel, set()).add(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            async with _event_lock:
                subscribers = _event_channels.get(channel)
                if subscribers is not None:
                    subscribers.discard(ws)
                    if not subscribers:
                        _event_channels.pop(channel, None)

    return router
