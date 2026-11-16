from __future__ import annotations

from fastapi.testclient import TestClient


def _create_template(client: TestClient, headers: dict[str, str], *, tag: str = "triage") -> dict:
    response = client.post(
        "/api/prompt-templates",
        headers=headers,
        json={
            "tag": tag,
            "desc": "Investigate an alert",
            "prompt": "Assess this security event and list next steps.",
        },
    )
    assert response.status_code == 201
    return response.json()


def _create_enabled_user(client: TestClient, admin_headers: dict[str, str], username: str) -> str:
    response = client.post(
        "/api/users",
        headers=admin_headers,
        json={
            "username": username,
            "password": "Password123!",
            "email": f"{username}@example.com",
            "status": "enabled",
        },
    )
    assert response.status_code == 201
    login = client.post("/api/auth/login", json={"username": username, "password": "Password123!"})
    assert login.status_code == 200
    return str(login.json()["access_token"])


def test_prompt_template_crud_and_schema(
    client: TestClient,
    auth_headers: dict[str, str],
    hermes_home,
) -> None:
    unauthorized = client.get("/api/prompt-templates")
    assert unauthorized.status_code == 401

    created = _create_template(client, auth_headers)
    assert set(created) == {"id", "tag", "desc", "prompt", "create_time", "update_time"}
    assert created["tag"] == "triage"
    assert created["create_time"] == created["update_time"]
    assert (hermes_home / "aegis.db").exists()

    listed = client.get("/api/prompt-templates", headers=auth_headers)
    assert listed.status_code == 200
    assert listed.json()["templates"] == [created]

    updated = client.put(
        f"/api/prompt-templates/{created['id']}",
        headers=auth_headers,
        json={"tag": "triage", "desc": "Updated", "prompt": "Updated prompt"},
    )
    assert updated.status_code == 200
    assert updated.json()["id"] == created["id"]
    assert updated.json()["desc"] == "Updated"
    assert updated.json()["prompt"] == "Updated prompt"

    deleted = client.delete(f"/api/prompt-templates/{created['id']}", headers=auth_headers)
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "id": created["id"]}
    assert client.get("/api/prompt-templates", headers=auth_headers).json() == {"templates": []}


def test_prompt_templates_are_user_isolated_and_validate_input(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    created = _create_template(client, auth_headers)
    analyst_token = _create_enabled_user(client, auth_headers, "analyst")
    analyst_headers = {"Authorization": f"Bearer {analyst_token}"}

    assert client.get("/api/prompt-templates", headers=analyst_headers).json() == {"templates": []}
    assert client.put(
        f"/api/prompt-templates/{created['id']}",
        headers=analyst_headers,
        json={"tag": "other", "desc": "", "prompt": "Should not update"},
    ).status_code == 404
    assert client.delete(f"/api/prompt-templates/{created['id']}", headers=analyst_headers).status_code == 404

    invalid = client.post(
        "/api/prompt-templates",
        headers=auth_headers,
        json={"tag": "   ", "desc": "", "prompt": "   "},
    )
    assert invalid.status_code == 422
