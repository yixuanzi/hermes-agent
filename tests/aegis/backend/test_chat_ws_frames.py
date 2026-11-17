"""The chat socket survives bad frames and always releases its actor.

A non-JSON text frame raised ``JSONDecodeError`` out of ``receive_json`` and closed the socket
with no code, and an ``HTTPException`` from quick-command resolution did the same. The actor was
released only on ``WebSocketDisconnect``, so after either it kept the dead socket and stayed
pinned against cache eviction.
"""

from __future__ import annotations

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from tests.aegis.backend.test_chat_ws import AUTH_TOKEN, _recv_until, _StreamingAgent
from tests.waiting import wait_until


_ADMIN_UID = "0000000000000001"
_WS_URL = f"/api/chat/ws?token={AUTH_TOKEN}"


def _app(load_backend):
    server = load_backend("aegis.backend.server")
    app = server.create_app()
    app.state.chat_manager.set_agent_factory(lambda session_id: _StreamingAgent(session_id))
    return app


def test_bad_frames_answer_invalid_message_and_the_socket_keeps_serving(
    load_backend, hermes_home
) -> None:
    app = _app(load_backend)
    with TestClient(app) as client, client.websocket_connect(_WS_URL) as ws:
        ws.send_text("this is not json")
        assert _recv_until(ws, "error")["code"] == "invalid_message"
        ws.send_bytes(b"\x00\x01")
        assert _recv_until(ws, "error")["code"] == "invalid_message"
        ws.send_text("[1, 2]")
        assert _recv_until(ws, "error")["code"] == "invalid_message"

        ws.send_json({"type": "session.bind", "title": "Still alive"})
        _recv_until(ws, "session.bound")
        ws.send_json({"type": "message.send", "text": "hi"})
        assert _recv_until(ws, "message.completed")["content"] == "hello world: hi"


def test_http_exception_while_sending_is_an_error_event(
    load_backend, hermes_home, monkeypatch
) -> None:
    app = _app(load_backend)

    def _reject(*_args, **_kwargs):
        raise HTTPException(status_code=500, detail="Stored agent 'nopath' has an invalid shape.")

    monkeypatch.setattr(app.state.quick_command_service, "resolve_text", _reject)
    with TestClient(app) as client, client.websocket_connect(_WS_URL) as ws:
        ws.send_json({"type": "session.bind", "title": "Rejected"})
        _recv_until(ws, "session.bound")
        ws.send_json({"type": "message.send", "text": "/help", "client_msg_id": "c-1"})
        error = _recv_until(ws, "error")
        assert error["message"] == "Stored agent 'nopath' has an invalid shape."
        assert error["client_msg_id"] == "c-1"

        ws.send_json({"type": "session.resume"})
        assert _recv_until(ws, "run.state")["state"] == "idle"


def test_the_actor_is_released_when_the_socket_dies_on_an_unexpected_error(
    load_backend, hermes_home, monkeypatch
) -> None:
    app = _app(load_backend)
    manager = app.state.chat_manager

    def _boom():
        raise RuntimeError("unexpected")

    with TestClient(app) as client:
        with pytest.raises(RuntimeError, match="unexpected"):
            with client.websocket_connect(_WS_URL) as ws:
                ws.send_json({"type": "session.bind", "title": "Doomed"})
                session_id = _recv_until(ws, "session.bound")["session_id"]
                actor = manager.get(session_id, user_id=_ADMIN_UID)
                monkeypatch.setattr(actor, "resume_state_events", _boom)
                ws.send_json({"type": "session.resume"})
                ws.receive_json()

    wait_until(actor.is_evictable, what="the dead socket released from the actor")
