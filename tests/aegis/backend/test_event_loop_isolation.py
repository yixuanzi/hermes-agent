"""A slow A2A peer must not stall the Aegis event loop.

``a2a_list()`` probes every agent card with a blocking ``httpx.get(timeout=5.0)``. The A2A context
routes and the chat socket's ``session.bind`` (which builds the agent, refreshing the A2A context)
ran it on the event loop, so ``/health`` and every other request waited behind one agent that never
answered.
"""

from __future__ import annotations

import json
import threading

from fastapi.testclient import TestClient

from tests.aegis.backend.test_chat_ws import AUTH_TOKEN, _recv_until, _StreamingAgent


_DEADLINE = 10.0
# The parked call outlasts the probe's deadline by far, so on a blocked loop /health cannot slip in
# when the parked call gives up on its own; the test releases it explicitly.
_PARK_LIMIT = 60.0


class _HealthProbe:
    """``GET /health`` on its own thread, started only once the blocking call is parked."""

    def __init__(self, client: TestClient) -> None:
        self._statuses: list[int] = []
        self._thread = threading.Thread(
            target=lambda: self._statuses.append(client.get("/health").status_code),
            daemon=True,
        )

    def answered(self) -> bool:
        self._thread.start()
        self._thread.join(_DEADLINE)
        return self._statuses == [200]

    def finish(self) -> None:
        if self._thread.ident is not None:
            self._thread.join(_PARK_LIMIT)


def test_a2a_context_refresh_does_not_block_health(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    from tools import a2a_delegate_tool

    entered, release = threading.Event(), threading.Event()

    def _unresponsive_agent_a2a_list(otype: str = "json") -> str:
        entered.set()
        release.wait(_PARK_LIMIT)
        return json.dumps({"success": True, "agents": [], "global_routing": []})

    monkeypatch.setattr(a2a_delegate_tool, "a2a_list", _unresponsive_agent_a2a_list)
    refresh = threading.Thread(
        target=lambda: client.post("/api/a2a/context/refresh", headers=auth_headers),
        daemon=True,
    )
    probe = _HealthProbe(client)
    refresh.start()
    try:
        assert entered.wait(_DEADLINE)
        answered = probe.answered()
    finally:
        release.set()
        probe.finish()
        refresh.join(_PARK_LIMIT)
    assert answered


def test_chat_bind_builds_the_agent_without_blocking_health(load_backend, hermes_home) -> None:
    server = load_backend("aegis.backend.server")
    app = server.create_app()
    entered, release = threading.Event(), threading.Event()

    def _factory_probing_an_unresponsive_agent(session_id: str) -> _StreamingAgent:
        entered.set()
        release.wait(_PARK_LIMIT)
        return _StreamingAgent(session_id)

    app.state.chat_manager.set_agent_factory(_factory_probing_an_unresponsive_agent)

    with TestClient(app) as client, client.websocket_connect(f"/api/chat/ws?token={AUTH_TOKEN}") as ws:
        probe = _HealthProbe(client)
        ws.send_json({"type": "session.bind", "title": "Slow agent"})
        try:
            assert entered.wait(_DEADLINE)
            answered = probe.answered()
        finally:
            release.set()
            probe.finish()
        assert answered
        assert _recv_until(ws, "session.bound")["title"] == "Slow agent"
