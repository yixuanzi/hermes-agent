from __future__ import annotations

import json

from fastapi.testclient import TestClient


def _context_payload(*, name: str = "aisoc", capability: str = "Investigate incidents") -> str:
    return json.dumps(
        {
            "success": True,
            "error": None,
            "global_routing": [],
            "agents": [
                {
                    "name": name,
                    "url": "http://127.0.0.1:9086/a2a",
                    "status": "active",
                    "available": True,
                    "description": "A2A test endpoint",
                    "capabilities": [capability],
                    "error": None,
                }
            ],
        },
        ensure_ascii=False,
    )


def test_a2a_context_requires_auth_and_uses_a_process_snapshot(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    from tools import a2a_delegate_tool

    calls = 0

    def fake_a2a_list(otype: str = "json") -> str:
        nonlocal calls
        assert otype == "json"
        calls += 1
        return _context_payload(capability=f"Capability {calls}")

    monkeypatch.setattr(a2a_delegate_tool, "a2a_list", fake_a2a_list)

    assert client.get("/api/a2a/context").status_code == 401

    first = client.get("/api/a2a/context", headers=auth_headers)
    assert first.status_code == 200
    assert calls == 1
    assert first.json()["agents"][0]["capabilities"] == ["Capability 1"]
    assert first.json()["stale"] is False
    assert first.json()["refreshed_at"]

    cached = client.get("/api/a2a/context", headers=auth_headers)
    assert cached.status_code == 200
    assert calls == 1
    assert cached.json() == first.json()

    refreshed = client.post("/api/a2a/context/refresh", headers=auth_headers)
    assert refreshed.status_code == 200
    assert calls == 2
    assert refreshed.json()["agents"][0]["capabilities"] == ["Capability 2"]


def test_a2a_context_keeps_last_snapshot_when_refresh_fails(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    from tools import a2a_delegate_tool

    monkeypatch.setattr(a2a_delegate_tool, "a2a_list", lambda _otype="json": _context_payload())
    first = client.get("/api/a2a/context", headers=auth_headers)
    assert first.status_code == 200

    monkeypatch.setattr(
        a2a_delegate_tool,
        "a2a_list",
        lambda _otype="json": json.dumps({"success": False, "error": "registry unavailable"}),
    )
    failed_refresh = client.post("/api/a2a/context/refresh", headers=auth_headers)
    assert failed_refresh.status_code == 200
    assert failed_refresh.json()["agents"] == first.json()["agents"]
    assert failed_refresh.json()["stale"] is True
    assert failed_refresh.json()["refresh_error"] == "registry unavailable"


def test_a2a_context_exposes_initial_refresh_failure_as_an_empty_error_snapshot(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    from tools import a2a_delegate_tool

    monkeypatch.setattr(
        a2a_delegate_tool,
        "a2a_list",
        lambda _otype="json": json.dumps({"success": False, "error": "registry unavailable"}),
    )

    response = client.get("/api/a2a/context", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {
        "agents": [],
        "global_routing": [],
        "refreshed_at": None,
        "stale": True,
        "refresh_error": "registry unavailable",
    }
