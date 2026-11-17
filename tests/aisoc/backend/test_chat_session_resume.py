"""Re-binding a session listed by ``/api/sessions`` resumes it instead of forking a new one.

The sessions page lists runtime ids (sha256 of user + public id); the chat UI binds with them.
"""

from __future__ import annotations

import time
from typing import Any

import jwt
from fastapi.testclient import TestClient


JWT_SECRET = "test-jwt-secret-1234567890-abcdef"


def _token(uid: str, username: str) -> str:
    return jwt.encode(
        {"sub": uid, "username": username, "email": f"{username}@aisoc.local", "iat": 1, "exp": 4102444800},
        JWT_SECRET,
        algorithm="HS256",
    )


def _recv_until(ws, event_type: str, *, timeout: float = 3.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = ws.receive_json()
        if payload.get("type") == event_type:
            return payload
    raise AssertionError(f"Timed out waiting for event type={event_type!r}")


class _RecordingAgent:
    def __init__(self, session_id: str, seen: list[str]):
        self.session_id = session_id
        self.stream_delta_callback = None
        self.tool_start_callback = None
        self.tool_complete_callback = None
        seen.append(session_id)

    def run_conversation(self, user_message: str, **_kwargs: Any) -> dict[str, Any]:
        return {"final_response": "ok", "completed": True}


def _bind(client: TestClient, token: str, session_id: str | None) -> str:
    with client.websocket_connect(f"/api/chat/session?token={token}") as ws:
        ws.send_json({"type": "session.bind", "session_id": session_id, "title": "t"})
        return _recv_until(ws, "session.bound")["session_id"]


def test_binding_an_owned_runtime_session_id_resumes_that_session(load_backend, hermes_home) -> None:
    from hermes_state import SessionDB

    server = load_backend("aisoc.backend.server")
    app = server.create_app()
    seen: list[str] = []
    app.state.chat_manager.set_agent_factory(lambda session_id: _RecordingAgent(session_id, seen))
    admin = _token("0000000000000001", "admin")

    with TestClient(app) as client:
        _bind(client, admin, "aisoc-public-chat")
        listed_runtime_id = seen[-1]
        db = SessionDB()
        try:
            db.create_session(listed_runtime_id, "aisoc_web", user_id="0000000000000001")
        finally:
            db.close()

        assert _bind(client, admin, listed_runtime_id) == listed_runtime_id

    assert seen[-1] == listed_runtime_id


def test_another_users_runtime_session_id_is_not_resumed(load_backend, hermes_home) -> None:
    from hermes_state import SessionDB

    server = load_backend("aisoc.backend.server")
    app = server.create_app()
    seen: list[str] = []
    app.state.chat_manager.set_agent_factory(lambda session_id: _RecordingAgent(session_id, seen))
    db = SessionDB()
    try:
        db.create_session("aisoc-owned-by-someone-else", "aisoc_web", user_id="0000000000000002")
    finally:
        db.close()

    with TestClient(app) as client:
        _bind(client, _token("0000000000000001", "admin"), "aisoc-owned-by-someone-else")

    assert seen[-1] != "aisoc-owned-by-someone-else"
