from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient


AUTH_TOKEN = jwt.encode(
    {
        "sub": "0000000000000001",
        "username": "admin",
        "email": "admin@aegis.local",
        "iat": 1,
        "exp": 4102444800,
    },
    "test-jwt-secret-1234567890-abcdef",
    algorithm="HS256",
)
AUTH_HEADERS = {"Authorization": f"Bearer {AUTH_TOKEN}"}


def _write_store(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _create_non_admin_headers(client: TestClient) -> dict[str, str]:
    create_response = client.post(
        "/api/users",
        headers=AUTH_HEADERS,
        json={
            "username": "analyst",
            "password": "Password123!",
            "email": "analyst@example.com",
            "status": "enabled",
        },
    )
    assert create_response.status_code == 201

    login_response = client.post(
        "/api/auth/login",
        json={"username": "analyst", "password": "Password123!"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def agent_client(
    load_backend,
    monkeypatch: pytest.MonkeyPatch,
    hermes_home,
) -> Iterator[TestClient]:
    monkeypatch.setenv("AEGIS_JWT_SECRET", "test-jwt-secret-1234567890-abcdef")

    server = load_backend("aegis.backend.server")
    app = server.create_app()
    with TestClient(app) as test_client:
        yield test_client


def test_agents_crud_persists_through_shared_store(
    agent_client: TestClient,
    hermes_home,
) -> None:
    create_response = agent_client.post(
        "/api/agents/agent-1",
        headers=AUTH_HEADERS,
        json={
            "url": "http://127.0.0.1:9086/a2a",
            "description": "A2A test endpoint",
            "headers": {"Authorization": "Bearer abc"},
            "status": "active",
            "extcapabilities": ["query-ip", "query-domain"],
        },
    )
    assert create_response.status_code == 201
    assert create_response.json() == {
        "agent_id": "agent-1",
        "url": "http://127.0.0.1:9086/a2a",
        "description": "A2A test endpoint",
        "headers": {"Authorization": "Bearer abc"},
        "status": "active",
        "extcapabilities": ["query-ip", "query-domain"],
    }

    list_response = agent_client.get("/api/agents", headers=AUTH_HEADERS)
    assert list_response.status_code == 200
    assert list_response.json() == {
        "agents": [
            {
                "agent_id": "agent-1",
                "url": "http://127.0.0.1:9086/a2a",
                "description": "A2A test endpoint",
                "headers": {"Authorization": "Bearer abc"},
                "status": "active",
                "extcapabilities": ["query-ip", "query-domain"],
            }
        ]
    }

    get_response = agent_client.get("/api/agents/agent-1", headers=AUTH_HEADERS)
    assert get_response.status_code == 200
    assert get_response.json() == {
        "agent_id": "agent-1",
        "url": "http://127.0.0.1:9086/a2a",
        "description": "A2A test endpoint",
        "headers": {"Authorization": "Bearer abc"},
        "status": "active",
        "extcapabilities": ["query-ip", "query-domain"],
    }

    update_response = agent_client.put(
        "/api/agents/agent-1",
        headers=AUTH_HEADERS,
        json={
            "url": "http://127.0.0.1:9087/a2a",
            "description": "Updated endpoint",
            "headers": {"X-Token": "def"},
            "status": "idle",
            "extcapabilities": ["query-url"],
        },
    )
    assert update_response.status_code == 200
    assert update_response.json() == {
        "agent_id": "agent-1",
        "url": "http://127.0.0.1:9087/a2a",
        "description": "Updated endpoint",
        "headers": {"X-Token": "def"},
        "status": "idle",
        "extcapabilities": ["query-url"],
    }

    assert json.loads((hermes_home / "a2a.json").read_text()) == {
        "a2a": {
            "agent-1": {
                "url": "http://127.0.0.1:9087/a2a",
                "description": "Updated endpoint",
                "headers": {"X-Token": "def"},
                "status": "idle",
                "extcapabilities": ["query-url"],
            }
        },
        "global": [],
    }

    delete_response = agent_client.delete("/api/agents/agent-1", headers=AUTH_HEADERS)
    assert delete_response.status_code == 200
    assert delete_response.json() == {"deleted": True, "agent_id": "agent-1"}
    assert json.loads((hermes_home / "a2a.json").read_text()) == {"a2a": {}, "global": []}


def test_agent_routes_require_admin_access(agent_client: TestClient) -> None:
    user_headers = _create_non_admin_headers(agent_client)

    list_response = agent_client.get("/api/agents", headers=user_headers)
    assert list_response.status_code == 403
    assert list_response.json() == {"detail": "Admin access required."}

    create_response = agent_client.post(
        "/api/agents/agent-1",
        headers=user_headers,
        json={
            "url": "http://127.0.0.1:9086/a2a",
            "description": "A2A test endpoint",
            "headers": {},
            "status": "active",
            "extcapabilities": [],
        },
    )
    assert create_response.status_code == 403
    assert create_response.json() == {"detail": "Admin access required."}


def test_overview_agents_route_returns_sanitized_agent_data_for_authenticated_users(
    agent_client: TestClient,
) -> None:
    create_response = agent_client.post(
        "/api/agents/agent-1",
        headers=AUTH_HEADERS,
        json={
            "url": "http://127.0.0.1:9086/a2a",
            "description": "A2A test endpoint",
            "headers": {"Authorization": "Bearer secret"},
            "status": "active",
            "extcapabilities": ["query-ip", "query-domain"],
        },
    )
    assert create_response.status_code == 201

    user_headers = _create_non_admin_headers(agent_client)
    overview_response = agent_client.get("/api/overview/agents", headers=user_headers)
    assert overview_response.status_code == 200
    assert overview_response.json() == {
        "agents": [
            {
                "agent_id": "agent-1",
                "url": "http://127.0.0.1:9086/a2a",
                "description": "A2A test endpoint",
                "status": "active",
                "extcapabilities": ["query-ip", "query-domain"],
            }
        ]
    }


def test_agent_create_and_global_rule_create_share_same_persisted_store(
    load_backend,
    agent_client: TestClient,
    hermes_home,
) -> None:
    agent_response = agent_client.post(
        "/api/agents/agent-1",
        headers=AUTH_HEADERS,
        json={
            "url": "http://127.0.0.1:9086/a2a",
            "description": "A2A test endpoint",
            "headers": {"Authorization": "Bearer abc"},
            "status": "active",
            "extcapabilities": ["query-ip"],
        },
    )
    assert agent_response.status_code == 201

    rule_response = agent_client.post(
        "/api/routing/global",
        headers=AUTH_HEADERS,
        json={
            "name": "Prioritize phishing incidents",
            "policy": "Route all phishing investigations to the email response queue.",
            "status": "active",
        },
    )
    assert rule_response.status_code == 201
    created_rule = rule_response.json()

    assert json.loads((hermes_home / "a2a.json").read_text()) == {
        "a2a": {
            "agent-1": {
                "url": "http://127.0.0.1:9086/a2a",
                "description": "A2A test endpoint",
                "headers": {"Authorization": "Bearer abc"},
                "status": "active",
                "extcapabilities": ["query-ip"],
            }
        },
        "global": [
            {
                "id": created_rule["id"],
                "name": "Prioritize phishing incidents",
                "policy": "Route all phishing investigations to the email response queue.",
                "status": "active",
            }
        ],
    }

    server = load_backend("aegis.backend.server")
    reloaded_app = server.create_app()
    with TestClient(reloaded_app) as reloaded_client:
        reloaded_agent = reloaded_client.get("/api/agents/agent-1", headers=AUTH_HEADERS)
        assert reloaded_agent.status_code == 200
        assert reloaded_agent.json() == {
            "agent_id": "agent-1",
            "url": "http://127.0.0.1:9086/a2a",
            "description": "A2A test endpoint",
            "headers": {"Authorization": "Bearer abc"},
            "status": "active",
            "extcapabilities": ["query-ip"],
        }

        reloaded_rules = reloaded_client.get("/api/routing/global", headers=AUTH_HEADERS)
        assert reloaded_rules.status_code == 200
        assert reloaded_rules.json() == {
            "rules": [
                {
                    "id": created_rule["id"],
                    "name": "Prioritize phishing incidents",
                    "policy": "Route all phishing investigations to the email response queue.",
                    "status": "active",
                }
            ]
        }


def test_post_rejects_duplicate_agent(agent_client: TestClient) -> None:
    payload = {
        "url": "http://127.0.0.1:9086/a2a",
        "description": "A2A test endpoint",
        "headers": {},
        "status": "active",
        "extcapabilities": [],
    }

    first = agent_client.post("/api/agents/agent-1", headers=AUTH_HEADERS, json=payload)
    assert first.status_code == 201

    second = agent_client.post("/api/agents/agent-1", headers=AUTH_HEADERS, json=payload)
    assert second.status_code == 409
    assert second.json() == {"detail": "Agent 'agent-1' already exists."}


def test_get_put_and_delete_return_404_for_missing_agent(agent_client: TestClient) -> None:
    get_response = agent_client.get("/api/agents/missing", headers=AUTH_HEADERS)
    assert get_response.status_code == 404
    assert get_response.json() == {"detail": "Agent 'missing' not found."}

    put_response = agent_client.put(
        "/api/agents/missing",
        headers=AUTH_HEADERS,
        json={
            "url": "http://127.0.0.1:9086/a2a",
            "description": "A2A test endpoint",
            "headers": {},
            "status": "offline",
            "extcapabilities": [],
        },
    )
    assert put_response.status_code == 404
    assert put_response.json() == {"detail": "Agent 'missing' not found."}

    delete_response = agent_client.delete("/api/agents/missing", headers=AUTH_HEADERS)
    assert delete_response.status_code == 404
    assert delete_response.json() == {"detail": "Agent 'missing' not found."}


def test_request_body_must_not_contain_agent_id(agent_client: TestClient) -> None:
    response = agent_client.post(
        "/api/agents/agent-1",
        headers=AUTH_HEADERS,
        json={
            "agent_id": "other-agent",
            "url": "http://127.0.0.1:9086/a2a",
            "description": "A2A test endpoint",
            "headers": {},
            "status": "active",
            "extcapabilities": [],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "extra_forbidden"


def test_status_allows_only_expected_values(agent_client: TestClient) -> None:
    response = agent_client.post(
        "/api/agents/agent-1",
        headers=AUTH_HEADERS,
        json={
            "url": "http://127.0.0.1:9086/a2a",
            "description": "A2A test endpoint",
            "headers": {},
            "status": "paused",
            "extcapabilities": [],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "status"]


def test_list_agents_supports_legacy_string_persisted_entries(
    load_backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("AEGIS_JWT_SECRET", "test-jwt-secret-1234567890-abcdef")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_store(
        tmp_path / "a2a.json",
        {
            "a2a": {
                "legacy-agent": "http://127.0.0.1:9086/a2a",
            },
            "global": [],
        },
    )

    server = load_backend("aegis.backend.server")
    app = server.create_app()
    with TestClient(app) as client:
        response = client.get("/api/agents", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {
        "agents": [
            {
                "agent_id": "legacy-agent",
                "url": "http://127.0.0.1:9086/a2a",
                "description": "",
                "headers": {},
                "status": "offline",
                "extcapabilities": [],
            }
        ]
    }


def test_list_agents_normalizes_legacy_string_entry_without_scheme(
    load_backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("AEGIS_JWT_SECRET", "test-jwt-secret-1234567890-abcdef")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_store(
        tmp_path / "a2a.json",
        {
            "a2a": {
                "legacy-agent": "127.0.0.1:9086/a2a",
            },
            "global": [],
        },
    )

    server = load_backend("aegis.backend.server")
    app = server.create_app()
    with TestClient(app) as client:
        response = client.get("/api/agents", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {
        "agents": [
            {
                "agent_id": "legacy-agent",
                "url": "http://127.0.0.1:9086/a2a",
                "description": "",
                "headers": {},
                "status": "offline",
                "extcapabilities": [],
            }
        ]
    }


def test_get_agent_returns_controlled_error_for_malformed_persisted_entry(
    load_backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("AEGIS_JWT_SECRET", "test-jwt-secret-1234567890-abcdef")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_store(
        tmp_path / "a2a.json",
        {
            "a2a": {
                "broken-agent": {
                    "url": "http://127.0.0.1:9086/a2a",
                    "description": "A2A endpoint",
                    "headers": [],
                    "status": "active",
                    "extcapabilities": [],
                }
            },
            "global": [],
        },
    )

    server = load_backend("aegis.backend.server")
    app = server.create_app()
    with TestClient(app) as client:
        response = client.get("/api/agents/broken-agent", headers=AUTH_HEADERS)

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Stored agent 'broken-agent' has an invalid shape.",
    }


def test_create_rejects_invalid_url(agent_client: TestClient) -> None:
    response = agent_client.post(
        "/api/agents/agent-1",
        headers=AUTH_HEADERS,
        json={
            "url": "not-a-url",
            "description": "A2A test endpoint",
            "headers": {},
            "status": "active",
            "extcapabilities": [],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "url"]


def test_create_normalizes_url_without_scheme(agent_client: TestClient, hermes_home) -> None:
    response = agent_client.post(
        "/api/agents/agent-1",
        headers=AUTH_HEADERS,
        json={
            "url": "127.0.0.1:9086/a2a",
            "description": "A2A test endpoint",
            "headers": {},
            "status": "active",
            "extcapabilities": [],
        },
    )

    assert response.status_code == 201
    assert response.json()["url"] == "http://127.0.0.1:9086/a2a"
    assert json.loads((hermes_home / "a2a.json").read_text()) == {
        "a2a": {
            "agent-1": {
                "url": "http://127.0.0.1:9086/a2a",
                "description": "A2A test endpoint",
                "headers": {},
                "status": "active",
                "extcapabilities": [],
            }
        },
        "global": [],
    }


def test_update_rejects_invalid_url(agent_client: TestClient) -> None:
    create_response = agent_client.post(
        "/api/agents/agent-1",
        headers=AUTH_HEADERS,
        json={
            "url": "https://127.0.0.1:9086/a2a",
            "description": "A2A test endpoint",
            "headers": {},
            "status": "active",
            "extcapabilities": [],
        },
    )
    assert create_response.status_code == 201

    response = agent_client.put(
        "/api/agents/agent-1",
        headers=AUTH_HEADERS,
        json={
            "url": "ftp://127.0.0.1:9087/a2a",
            "description": "Updated endpoint",
            "headers": {},
            "status": "idle",
            "extcapabilities": [],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "url"]
