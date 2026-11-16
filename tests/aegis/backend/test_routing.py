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
def routing_client(
    load_backend,
    monkeypatch: pytest.MonkeyPatch,
    hermes_home,
) -> Iterator[TestClient]:
    monkeypatch.setenv("AEGIS_JWT_SECRET", "test-jwt-secret-1234567890-abcdef")

    server = load_backend("aegis.backend.server")
    app = server.create_app()
    with TestClient(app) as test_client:
        yield test_client


def test_global_routing_crud_persists_through_shared_store(
    routing_client: TestClient,
    hermes_home,
) -> None:
    create_response = routing_client.post(
        "/api/routing/global",
        headers=AUTH_HEADERS,
        json={
            "name": "Prioritize phishing incidents",
            "policy": "Route all phishing investigations to the email response queue.",
            "status": "active",
        },
    )
    assert create_response.status_code == 201
    created_rule = create_response.json()
    assert created_rule["id"]
    assert len(created_rule["id"]) == 8
    assert created_rule == {
        "id": created_rule["id"],
        "name": "Prioritize phishing incidents",
        "policy": "Route all phishing investigations to the email response queue.",
        "status": "active",
    }

    list_response = routing_client.get("/api/routing/global", headers=AUTH_HEADERS)
    assert list_response.status_code == 200
    assert list_response.json() == {"rules": [created_rule]}

    get_response = routing_client.get(
        f"/api/routing/global/{created_rule['id']}",
        headers=AUTH_HEADERS,
    )
    assert get_response.status_code == 200
    assert get_response.json() == created_rule

    update_response = routing_client.put(
        f"/api/routing/global/{created_rule['id']}",
        headers=AUTH_HEADERS,
        json={
            "name": "Fallback phishing queue",
            "policy": "Fallback unmatched phishing reports to the triage queue.",
            "status": "inactive",
        },
    )
    assert update_response.status_code == 200
    assert update_response.json() == {
        "id": created_rule["id"],
        "name": "Fallback phishing queue",
        "policy": "Fallback unmatched phishing reports to the triage queue.",
        "status": "inactive",
    }

    assert json.loads((hermes_home / "a2a.json").read_text()) == {
        "a2a": {},
        "global": [
            {
                "id": created_rule["id"],
                "name": "Fallback phishing queue",
                "policy": "Fallback unmatched phishing reports to the triage queue.",
                "status": "inactive",
            }
        ],
    }

    delete_response = routing_client.delete(
        f"/api/routing/global/{created_rule['id']}",
        headers=AUTH_HEADERS,
    )
    assert delete_response.status_code == 200
    assert delete_response.json() == {"deleted": True, "id": created_rule["id"]}
    assert json.loads((hermes_home / "a2a.json").read_text()) == {"a2a": {}, "global": []}


def test_routing_routes_require_admin_access(routing_client: TestClient) -> None:
    user_headers = _create_non_admin_headers(routing_client)

    list_response = routing_client.get("/api/routing/global", headers=user_headers)
    assert list_response.status_code == 403
    assert list_response.json() == {"detail": "Admin access required."}

    create_response = routing_client.post(
        "/api/routing/global",
        headers=user_headers,
        json={
            "name": "Prioritize phishing incidents",
            "policy": "Route all phishing investigations to the email response queue.",
            "status": "active",
        },
    )
    assert create_response.status_code == 403
    assert create_response.json() == {"detail": "Admin access required."}


def test_create_global_routing_rule_avoids_duplicate_generated_ids(
    load_backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("AEGIS_JWT_SECRET", "test-jwt-secret-1234567890-abcdef")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_store(
        tmp_path / "a2a.json",
        {
            "a2a": {},
            "global": [
                {
                    "id": "deadbeef",
                    "name": "Existing rule",
                    "policy": "Keep existing routing.",
                    "status": "active",
                }
            ],
        },
    )

    routing_service = load_backend("aegis.backend.services.routing_service")
    generated_chars = iter("deadbeeffacecafe")
    monkeypatch.setattr(
        routing_service.secrets,
        "choice",
        lambda _alphabet: next(generated_chars),
    )

    server = load_backend("aegis.backend.server")
    app = server.create_app()
    with TestClient(app) as routing_client:
        response = routing_client.post(
            "/api/routing/global",
            headers=AUTH_HEADERS,
            json={
                "name": "New rule",
                "policy": "Send new cases to the fallback queue.",
                "status": "active",
            },
        )

    assert response.status_code == 201
    assert response.json()["id"] == "facecafe"
    assert [rule["id"] for rule in json.loads((tmp_path / "a2a.json").read_text())["global"]] == [
        "deadbeef",
        "facecafe",
    ]


def test_get_put_and_delete_return_404_for_missing_global_rule(
    routing_client: TestClient,
) -> None:
    get_response = routing_client.get("/api/routing/global/missing123", headers=AUTH_HEADERS)
    assert get_response.status_code == 404
    assert get_response.json() == {"detail": "Global routing rule 'missing123' not found."}

    put_response = routing_client.put(
        "/api/routing/global/missing123",
        headers=AUTH_HEADERS,
        json={
            "name": "Fallback queue",
            "policy": "Route unmatched alerts to the fallback queue.",
            "status": "inactive",
        },
    )
    assert put_response.status_code == 404
    assert put_response.json() == {"detail": "Global routing rule 'missing123' not found."}

    delete_response = routing_client.delete(
        "/api/routing/global/missing123",
        headers=AUTH_HEADERS,
    )
    assert delete_response.status_code == 404
    assert delete_response.json() == {"detail": "Global routing rule 'missing123' not found."}


def test_global_routing_request_body_forbids_extra_fields(
    routing_client: TestClient,
) -> None:
    response = routing_client.post(
        "/api/routing/global",
        headers=AUTH_HEADERS,
        json={
            "id": "rule-should-not-be-accepted",
            "name": "Fallback queue",
            "policy": "Route unmatched alerts to the fallback queue.",
            "status": "active",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "extra_forbidden"


def test_global_routing_status_allows_only_active_or_inactive(
    routing_client: TestClient,
) -> None:
    response = routing_client.post(
        "/api/routing/global",
        headers=AUTH_HEADERS,
        json={
            "name": "Fallback queue",
            "policy": "Route unmatched alerts to the fallback queue.",
            "status": "paused",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "status"]


def test_list_rules_returns_controlled_error_for_malformed_persisted_entry(
    load_backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("AEGIS_JWT_SECRET", "test-jwt-secret-1234567890-abcdef")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_store(
        tmp_path / "a2a.json",
        {
            "a2a": {},
            "global": ["bad-entry"],
        },
    )

    server = load_backend("aegis.backend.server")
    app = server.create_app()
    with TestClient(app) as client:
        response = client.get("/api/routing/global", headers=AUTH_HEADERS)

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Stored global routing rule is invalid.",
    }


def test_get_rule_returns_controlled_error_for_malformed_persisted_entry(
    load_backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("AEGIS_JWT_SECRET", "test-jwt-secret-1234567890-abcdef")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_store(
        tmp_path / "a2a.json",
        {
            "a2a": {},
            "global": [
                {
                    "id": "deadbeef",
                    "name": "Broken rule",
                    "policy": "Route unmatched alerts to the fallback queue.",
                    "status": "paused",
                }
            ],
        },
    )

    server = load_backend("aegis.backend.server")
    app = server.create_app()
    with TestClient(app) as client:
        response = client.get("/api/routing/global/deadbeef", headers=AUTH_HEADERS)

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Stored global routing rule 'deadbeef' has an invalid shape.",
    }


def test_get_put_and_delete_fail_closed_for_duplicate_ids_on_disk(
    load_backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("AEGIS_JWT_SECRET", "test-jwt-secret-1234567890-abcdef")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_store(
        tmp_path / "a2a.json",
        {
            "a2a": {},
            "global": [
                {
                    "id": "deadbeef",
                    "name": "First rule",
                    "policy": "First policy.",
                    "status": "active",
                },
                {
                    "id": "deadbeef",
                    "name": "Second rule",
                    "policy": "Second policy.",
                    "status": "inactive",
                },
            ],
        },
    )

    server = load_backend("aegis.backend.server")
    app = server.create_app()
    with TestClient(app) as client:
        get_response = client.get("/api/routing/global/deadbeef", headers=AUTH_HEADERS)
        put_response = client.put(
            "/api/routing/global/deadbeef",
            headers=AUTH_HEADERS,
            json={
                "name": "Updated rule",
                "policy": "Updated policy.",
                "status": "active",
            },
        )
        delete_response = client.delete("/api/routing/global/deadbeef", headers=AUTH_HEADERS)

    expected = {"detail": "Stored global routing rule ID 'deadbeef' is duplicated."}
    assert get_response.status_code == 500
    assert get_response.json() == expected
    assert put_response.status_code == 500
    assert put_response.json() == expected
    assert delete_response.status_code == 500
    assert delete_response.json() == expected
    assert json.loads((tmp_path / "a2a.json").read_text())["global"] == [
        {
            "id": "deadbeef",
            "name": "First rule",
            "policy": "First policy.",
            "status": "active",
        },
        {
            "id": "deadbeef",
            "name": "Second rule",
            "policy": "Second policy.",
            "status": "inactive",
        },
    ]


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        (
            "post",
            "/api/routing/global",
            {
                "name": "Created rule",
                "policy": "Created policy.",
                "status": "active",
            },
        ),
        (
            "put",
            "/api/routing/global/facecafe",
            {
                "name": "Updated rule",
                "policy": "Updated policy.",
                "status": "inactive",
            },
        ),
        ("delete", "/api/routing/global/facecafe", None),
    ],
)
def test_mutations_fail_closed_for_malformed_persisted_entries(
    load_backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    method: str,
    path: str,
    body: dict[str, str] | None,
) -> None:
    monkeypatch.setenv("AEGIS_JWT_SECRET", "test-jwt-secret-1234567890-abcdef")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_store(
        tmp_path / "a2a.json",
        {
            "a2a": {},
            "global": [
                {
                    "id": "facecafe",
                    "name": "Valid rule",
                    "policy": "Valid policy.",
                    "status": "active",
                },
                "bad-entry",
            ],
        },
    )

    server = load_backend("aegis.backend.server")
    app = server.create_app()
    with TestClient(app) as client:
        request = getattr(client, method)
        response = request(path, headers=AUTH_HEADERS, json=body) if body else request(
            path,
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 500
    assert response.json() == {"detail": "Stored global routing rule is invalid."}
    assert json.loads((tmp_path / "a2a.json").read_text())["global"] == [
        {
            "id": "facecafe",
            "name": "Valid rule",
            "policy": "Valid policy.",
            "status": "active",
        },
        "bad-entry",
    ]


def test_create_global_routing_rule_fails_when_id_generation_is_exhausted(
    load_backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("AEGIS_JWT_SECRET", "test-jwt-secret-1234567890-abcdef")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_store(
        tmp_path / "a2a.json",
        {
            "a2a": {},
            "global": [
                {
                    "id": "dddddddd",
                    "name": "Existing rule",
                    "policy": "Keep existing routing.",
                    "status": "active",
                }
            ],
        },
    )

    server = load_backend("aegis.backend.server")
    routing_service = server.build_routing_router.__globals__["RoutingService"].__module__
    routing_service_module = __import__(routing_service, fromlist=["_MAX_ID_GENERATION_ATTEMPTS"])
    monkeypatch.setattr(routing_service_module, "_MAX_ID_GENERATION_ATTEMPTS", 2)
    monkeypatch.setattr(
        routing_service_module.secrets,
        "choice",
        lambda _alphabet: "d",
    )
    app = server.create_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/routing/global",
            headers=AUTH_HEADERS,
            json={
                "name": "Created rule",
                "policy": "Created policy.",
                "status": "active",
            },
        )

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Unable to generate a unique global routing rule ID.",
    }
