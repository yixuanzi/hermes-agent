"""The chat socket survives bad input, releases its session on every exit,
keeps the event loop free while agents are built, and never opens a turn for
a message it rejects."""

from __future__ import annotations

import importlib
import threading
from typing import Any

import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


USER_ID = "0000000000000001"
AUTH_TOKEN = jwt.encode(
    {"sub": USER_ID, "username": "admin", "iat": 1, "exp": 4102444800},
    "test-jwt-secret-1234567890-abcdef",
    algorithm="HS256",
)
WS_URL = f"/api/chat/session?token={AUTH_TOKEN}"


class _Agent:
    def __init__(self, session_id: str, release: threading.Event | None = None):
        self.session_id = session_id
        self.stream_delta_callback = None
        self.tool_start_callback = None
        self.tool_complete_callback = None
        self._release = release

    def run_conversation(self, user_message: str, **_kwargs: Any) -> dict[str, Any]:
        if self._release is not None:
            self._release.wait(timeout=10)
        return {"final_response": f"done: {user_message}", "completed": True}


def _recv_until(ws, event_type: str, *, limit: int = 20) -> dict[str, Any]:
    for _ in range(limit):
        payload = ws.receive_json()
        if payload.get("type") == event_type:
            return payload
    raise AssertionError(f"no {event_type!r} event")


@pytest.fixture
def app(load_backend, hermes_home):
    server = load_backend("aisoc.backend.server")
    app = server.create_app()
    app.state.chat_manager.set_agent_factory(lambda session_id: _Agent(session_id))
    return app


@pytest.fixture
def detached(monkeypatch) -> list[str]:
    service = importlib.import_module("aisoc.backend.chat.service")
    calls: list[str] = []
    original = service.ChatSessionActor.detach_connection

    def _spy(self, websocket):
        calls.append(self.session_id)
        original(self, websocket)

    monkeypatch.setattr(service.ChatSessionActor, "detach_connection", _spy)
    return calls


def test_malformed_frames_get_an_error_event_and_the_socket_keeps_serving(app, detached):
    with TestClient(app) as client, client.websocket_connect(WS_URL) as ws:
        ws.send_json({"type": "session.bind", "title": "Robust"})
        session_id = _recv_until(ws, "session.bound")["session_id"]

        ws.send_text("{not json")
        assert ws.receive_json()["code"] == "invalid_message"
        ws.send_bytes(b"\x00\x01binary")
        assert ws.receive_json()["code"] == "invalid_message"

        ws.send_json({"type": "message.send", "text": "still alive", "client_msg_id": "m1"})
        assert _recv_until(ws, "message.completed")["content"] == "done: still alive"

    assert detached == [session_id]


def test_unexpected_handler_failure_still_releases_the_session(app, detached, monkeypatch):
    service = importlib.import_module("aisoc.backend.chat.service")

    def _boom(self):
        raise RuntimeError("resume exploded")

    monkeypatch.setattr(service.ChatSessionActor, "resume_state_events", _boom)
    bound: list[str] = []
    with TestClient(app) as client:
        # The test client re-raises the handler's exception when the socket closes.
        with pytest.raises(RuntimeError, match="resume exploded"):
            with client.websocket_connect(WS_URL) as ws:
                ws.send_json({"type": "session.bind"})
                bound.append(_recv_until(ws, "session.bound")["session_id"])
                ws.send_json({"type": "session.resume"})
                ws.receive_json()

    assert detached == bound


def test_rejected_message_resolution_is_an_error_event_not_a_dead_socket(app, monkeypatch):
    def _reject(text, *, args=None):
        raise HTTPException(status_code=404, detail="Unknown quick command.")

    monkeypatch.setattr(app.state.quick_command_service, "resolve_text", _reject)
    with TestClient(app) as client, client.websocket_connect(WS_URL) as ws:
        ws.send_json({"type": "session.bind"})
        _recv_until(ws, "session.bound")
        ws.send_json({"type": "message.send", "text": "@[prompt_x]", "client_msg_id": "m1"})
        error = ws.receive_json()
        assert (error["type"], error["code"], error["client_msg_id"]) == ("error", "message_rejected", "m1")
        ws.send_json({"type": "session.resume"})
        assert ws.receive_json()["type"] == "run.state"


def test_cold_session_bind_does_not_block_other_requests(app):
    entered = threading.Event()
    health_served = threading.Event()
    served_while_building: list[bool] = []

    def _slow_factory(session_id):
        entered.set()
        served_while_building.append(health_served.wait(timeout=5))
        return _Agent(session_id)

    app.state.chat_manager.set_agent_factory(_slow_factory)
    with TestClient(app) as client, client.websocket_connect(WS_URL) as ws:
        ws.send_json({"type": "session.bind"})
        assert entered.wait(timeout=5)
        assert client.get("/health").status_code == 200
        health_served.set()
        _recv_until(ws, "session.bound")

    assert served_while_building == [True]


def test_busy_session_rejects_a_message_without_accepting_it_first(app):
    release = threading.Event()
    app.state.chat_manager.set_agent_factory(lambda session_id: _Agent(session_id, release))
    with TestClient(app) as client, client.websocket_connect(WS_URL) as ws:
        ws.send_json({"type": "session.bind"})
        _recv_until(ws, "session.bound")
        ws.send_json({"type": "message.send", "text": "first", "client_msg_id": "m1"})
        _recv_until(ws, "message.accepted")

        ws.send_json({"type": "message.send", "text": "second", "client_msg_id": "m2"})
        seen = []
        while True:
            event = ws.receive_json()
            seen.append(event)
            if event["type"] == "error":
                break
        release.set()

    assert seen[-1]["code"] == "session_busy"
    assert seen[-1]["client_msg_id"] == "m2"
    assert not any(e["type"] == "message.accepted" for e in seen)
