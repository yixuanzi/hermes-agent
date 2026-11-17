"""``session.bound.resumed`` reports whether the session really had prior state.

It was ``bool(payload["session_id"])``, so a brand-new conversation whose id the client minted
was announced as resumed. It is true only for an existing in-memory actor or for turns SessionDB
persisted for that runtime session.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.aegis.backend.test_chat_ws import AUTH_TOKEN, _recv_until, _StreamingAgent


_WS_URL = f"/api/chat/ws?token={AUTH_TOKEN}"


class _PersistedSessionDB:
    def __init__(self, sessions: dict[str, list[dict[str, Any]]]) -> None:
        self._sessions = sessions

    def get_messages_as_conversation(self, session_id: str) -> list[dict[str, Any]]:
        return list(self._sessions.get(session_id, []))


def _bind(ws, session_id: str) -> bool:
    ws.send_json({"type": "session.bind", "session_id": session_id, "title": "Resume check"})
    return _recv_until(ws, "session.bound")["resumed"]


def test_new_client_minted_session_is_not_resumed_but_a_rebind_is(load_backend, hermes_home) -> None:
    server = load_backend("aegis.backend.server")
    app = server.create_app()
    app.state.chat_manager.set_agent_factory(lambda session_id: _StreamingAgent(session_id))

    with TestClient(app) as client:
        with client.websocket_connect(_WS_URL) as ws:
            assert _bind(ws, "client-minted-1") is False
        with client.websocket_connect(_WS_URL) as ws:
            assert _bind(ws, "client-minted-1") is True


def test_session_with_persisted_turns_is_resumed_after_a_restart(load_backend, hermes_home) -> None:
    server = load_backend("aegis.backend.server")
    persisted: dict[str, list[dict[str, Any]]] = {}
    runtime_session_ids: list[str] = []

    def _factory(session_id: str) -> _StreamingAgent:
        runtime_session_ids.append(session_id)
        agent = _StreamingAgent(session_id)
        agent._session_db = _PersistedSessionDB(persisted)
        return agent

    first = server.create_app()
    first.state.chat_manager.set_agent_factory(_factory)
    with TestClient(first) as client, client.websocket_connect(_WS_URL) as ws:
        assert _bind(ws, "persisted-1") is False
    persisted[runtime_session_ids[0]] = [{"role": "user", "content": "earlier turn"}]

    restarted = server.create_app()
    restarted.state.chat_manager.set_agent_factory(_factory)
    with TestClient(restarted) as client, client.websocket_connect(_WS_URL) as ws:
        assert _bind(ws, "persisted-1") is True
