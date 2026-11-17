"""One malformed ``a2a.json`` entry must not take down the surfaces built on the agent list.

A hand-added ``"nopath": "127.0.0.1:9086"`` (no scheme, no path) made ``/api/agents``,
``/api/overview/agents`` and ``/api/chat/quick-commands`` answer 500 and closed the chat socket
on every ``message.send``, even ``/help``: each built every stored agent and raised
``HTTPException(500)`` on the first that failed to parse.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from tests.aegis.backend.test_chat_ws import (
    AUTH_TOKEN,
    _recv_until,
    _recv_until_one_of,
    _StreamingAgent,
)


_HEADERS = {"Authorization": f"Bearer {AUTH_TOKEN}"}
_GOOD_AGENT = {
    "url": "http://127.0.0.1:9086/a2a",
    "description": "Threat intel",
    "headers": {},
    "status": "active",
    "extcapabilities": [],
}


@pytest.fixture
def app(load_backend, hermes_home: Path):
    (hermes_home / "a2a.json").write_text(
        json.dumps({"a2a": {"intel": _GOOD_AGENT, "nopath": "127.0.0.1:9086"}, "global": []}),
        encoding="utf-8",
    )
    server = load_backend("aegis.backend.server")
    app = server.create_app()
    app.state.chat_manager.set_agent_factory(lambda session_id: _StreamingAgent(session_id))
    return app


def test_invalid_entry_is_skipped_and_reported_without_failing_any_surface(app) -> None:
    with TestClient(app) as client:
        listing = client.get("/api/agents", headers=_HEADERS)
        assert listing.status_code == 200
        assert [agent["agent_id"] for agent in listing.json()["agents"]] == ["intel"]
        assert [entry["agent_id"] for entry in listing.json()["invalid"]] == ["nopath"]
        assert "path" in listing.json()["invalid"][0]["error"]

        overview = client.get("/api/overview/agents", headers=_HEADERS)
        assert overview.status_code == 200
        assert [agent["agent_id"] for agent in overview.json()["agents"]] == ["intel"]

        commands = client.get("/api/chat/quick-commands", headers=_HEADERS)
        assert commands.status_code == 200
        assert ("agent", "intel") in {(c["type"], c["name"]) for c in commands.json()["commands"]}

        with client.websocket_connect(f"/api/chat/ws?token={AUTH_TOKEN}") as ws:
            ws.send_json({"type": "session.bind", "title": "Help"})
            _recv_until(ws, "session.bound")
            ws.send_json({"type": "message.send", "text": "/help"})
            reply = _recv_until_one_of(ws, {"message.completed", "error"})
    assert reply["type"] == "message.completed"
    assert "/help" in reply["content"]


def test_invalid_entry_can_still_be_fixed_or_deleted_by_id(app) -> None:
    with TestClient(app) as client:
        fixed = client.put(
            "/api/agents/nopath",
            headers=_HEADERS,
            json={**_GOOD_AGENT, "url": "http://127.0.0.1:9087/a2a"},
        )
        after_fix = client.get("/api/agents", headers=_HEADERS).json()
        deleted = client.delete("/api/agents/nopath", headers=_HEADERS)
        after_delete = client.get("/api/agents", headers=_HEADERS).json()

    assert fixed.status_code == 200
    assert [agent["agent_id"] for agent in after_fix["agents"]] == ["intel", "nopath"]
    assert after_fix["invalid"] == []
    assert deleted.status_code == 200
    assert [agent["agent_id"] for agent in after_delete["agents"]] == ["intel"]
