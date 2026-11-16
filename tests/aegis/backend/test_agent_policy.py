from __future__ import annotations

import jwt
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


def _create_non_admin_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/users",
        headers=AUTH_HEADERS,
        json={
            "username": "analyst",
            "password": "Password123!",
            "email": "analyst@example.com",
            "status": "enabled",
        },
    )
    assert response.status_code == 201
    login = client.post(
        "/api/auth/login",
        json={"username": "analyst", "password": "Password123!"},
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_agent_policy_crud_uses_aegis_database(client: TestClient, hermes_home) -> None:
    create = client.post(
        "/api/routing/agent",
        headers=AUTH_HEADERS,
        json={
            "rank_id": 10,
            "platform": " AEGIS ",
            "user_id": "",
            "agent_name": "responder",
            "status": "deny",
        },
    )
    assert create.status_code == 201
    assert create.json() == {
        "rank_id": 10,
        "platform": "aegis",
        "user_id": "*",
        "agent_name": "responder",
        "status": "deny",
    }
    assert (hermes_home / "aegis.db").exists()

    assert client.get("/api/routing/agent", headers=AUTH_HEADERS).json() == {
        "policies": [create.json()]
    }
    assert client.get("/api/routing/agent/10", headers=AUTH_HEADERS).json() == create.json()

    update = client.put(
        "/api/routing/agent/10",
        headers=AUTH_HEADERS,
        json={
            "rank_id": 2,
            "platform": "*",
            "user_id": "u-1",
            "agent_name": "responder",
            "status": "allow",
        },
    )
    assert update.status_code == 200
    assert update.json()["rank_id"] == 2
    assert update.json()["status"] == "allow"

    missing = client.get("/api/routing/agent/10", headers=AUTH_HEADERS)
    assert missing.status_code == 404
    deleted = client.delete("/api/routing/agent/2", headers=AUTH_HEADERS)
    assert deleted.json() == {"deleted": True, "rank_id": 2}


def test_agent_policy_duplicate_rank_returns_conflict(client: TestClient) -> None:
    body = {
        "rank_id": 1,
        "platform": "*",
        "user_id": "*",
        "agent_name": "*",
        "status": "deny",
    }
    assert client.post("/api/routing/agent", headers=AUTH_HEADERS, json=body).status_code == 201
    response = client.post("/api/routing/agent", headers=AUTH_HEADERS, json=body)
    assert response.status_code == 409
    assert response.json() == {"detail": "Agent Policy rank '1' already exists."}

    second = {**body, "rank_id": 2, "status": "allow"}
    assert client.post("/api/routing/agent", headers=AUTH_HEADERS, json=second).status_code == 201
    update = client.put(
        "/api/routing/agent/2",
        headers=AUTH_HEADERS,
        json={**second, "rank_id": 1},
    )
    assert update.status_code == 409
    assert update.json() == {"detail": "Agent Policy rank '1' already exists."}


def test_agent_policy_validates_rank_status_and_extra_fields(client: TestClient) -> None:
    for body in (
        {"rank_id": 0, "platform": "*", "user_id": "*", "agent_name": "*", "status": "allow"},
        {"rank_id": 1, "platform": "*", "user_id": "*", "agent_name": "*", "status": "dany"},
        {
            "rank_id": 1,
            "platform": "*",
            "user_id": "*",
            "agent_name": "*",
            "status": "allow",
            "unexpected": True,
        },
    ):
        response = client.post("/api/routing/agent", headers=AUTH_HEADERS, json=body)
        assert response.status_code == 422


def test_agent_policy_routes_require_admin(client: TestClient) -> None:
    user_headers = _create_non_admin_headers(client)
    assert client.get("/api/routing/agent", headers=user_headers).status_code == 403
    response = client.post(
        "/api/routing/agent",
        headers=user_headers,
        json={
            "rank_id": 1,
            "platform": "*",
            "user_id": "*",
            "agent_name": "*",
            "status": "deny",
        },
    )
    assert response.status_code == 403
