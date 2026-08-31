from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from tests.aegis.backend.conftest import BOOTSTRAP_PASSWORD


def test_load_settings_generates_token_when_env_missing(
    load_backend,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AEGIS_JWT_SECRET", raising=False)

    config = load_backend("aegis.backend.config")
    settings = config.load_aegis_settings()

    assert settings.jwt_secret
    assert settings.jwt_expire_seconds == 28800


def test_lark_sso_enable_defaults_false_and_accepts_truthy_values(
    load_backend,
    monkeypatch,
) -> None:
    monkeypatch.delenv("LARK_SSO_ENABLE", raising=False)
    config = load_backend("aegis.backend.config")

    assert config.load_aegis_settings().lark_sso_enabled is False

    for value in ("1", "true", "yes", "on", "TRUE"):
        monkeypatch.setenv("LARK_SSO_ENABLE", value)
        assert config.load_aegis_settings().lark_sso_enabled is True

    for value in ("0", "false", "no", "off", "unexpected"):
        monkeypatch.setenv("LARK_SSO_ENABLE", value)
        assert config.load_aegis_settings().lark_sso_enabled is False


def test_login_session_and_logout_routes(client: TestClient) -> None:
    login_response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": BOOTSTRAP_PASSWORD},
    )
    assert login_response.status_code == 200
    payload = login_response.json()
    assert payload["authenticated"] is True
    assert payload["access_token"]
    assert payload["token_type"] == "bearer"
    assert payload["expires_in"] == 28800
    assert payload["user"]["username"] == "admin"
    assert payload["user"]["is_admin"] is True
    assert payload["user"]["uid"] == "0000000000000001"
    assert len(payload["user"]["uid"]) == 16

    session_without_auth = client.get("/api/auth/session")
    assert session_without_auth.status_code == 200
    assert session_without_auth.json() == {
        "authenticated": False,
    }

    session_with_auth = client.get(
        "/api/auth/session",
        headers={"Authorization": f"Bearer {payload['access_token']}"},
    )
    assert session_with_auth.status_code == 200
    session_payload = session_with_auth.json()
    assert session_payload["authenticated"] is True
    assert session_payload["user"]["username"] == "admin"
    assert len(session_payload["user"]["uid"]) == 16
    assert session_payload["expires_in"] > 0

    logout_response = client.post("/api/auth/logout")
    assert logout_response.status_code == 200
    assert logout_response.json() == {"logged_out": True}


def test_session_accepts_case_insensitive_bearer_schemes(client: TestClient) -> None:
    login_response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": BOOTSTRAP_PASSWORD},
    )
    token = login_response.json()["access_token"]

    lowercase = client.get(
        "/api/auth/session",
        headers={"Authorization": f"bearer {token}"},
    )
    assert lowercase.status_code == 200
    assert lowercase.json()["authenticated"] is True

    mixed_case = client.get(
        "/api/auth/session",
        headers={"Authorization": f"BeArEr {token}"},
    )
    assert mixed_case.status_code == 200
    assert mixed_case.json()["authenticated"] is True


def test_register_defaults_user_to_disabled_and_login_is_blocked(client: TestClient) -> None:
    register_response = client.post(
        "/api/auth/register",
        json={
            "username": "new-user",
            "password": "Password123!",
            "email": "new-user@example.com",
        },
    )
    assert register_response.status_code == 201
    assert register_response.json() == {"registered": True, "status": "disabled"}

    login_response = client.post(
        "/api/auth/login",
        json={"username": "new-user", "password": "Password123!"},
    )
    assert login_response.status_code == 403
    assert login_response.json() == {"detail": "User account is disabled."}


def test_change_password_updates_user_credentials(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    change_response = client.put(
        "/api/auth/password",
        headers=auth_headers,
        json={"old_password": BOOTSTRAP_PASSWORD, "new_password": "AdminPassword123!"},
    )
    assert change_response.status_code == 200
    assert change_response.json() == {"updated": True}

    old_login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": BOOTSTRAP_PASSWORD},
    )
    assert old_login.status_code == 401
    assert old_login.json() == {"detail": "Invalid username or password."}

    new_login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "AdminPassword123!"},
    )
    assert new_login.status_code == 200


def test_auth_middleware_protects_other_api_routes(client: TestClient) -> None:
    unauthorized = client.get("/api/does-not-exist")
    assert unauthorized.status_code == 401
    assert unauthorized.json() == {"detail": "Unauthorized"}

    login_response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": BOOTSTRAP_PASSWORD},
    )
    token = login_response.json()["access_token"]
    authorized = client.get(
        "/api/does-not-exist",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert authorized.status_code == 404
    assert authorized.json() == {"detail": "Not Found"}

    lowercase = client.get(
        "/api/does-not-exist",
        headers={"Authorization": f"bearer {token}"},
    )
    assert lowercase.status_code == 404
    assert lowercase.json() == {"detail": "Not Found"}


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
)
def test_auth_middleware_allows_cors_preflight_for_protected_routes(
    client: TestClient,
    origin: str,
) -> None:
    response = client.options(
        "/api/agents",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "GET" in response.headers["access-control-allow-methods"]


def test_public_health_and_bootstrap_routes_are_accessible(client: TestClient) -> None:
    health_response = client.get("/health")
    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok", "pid": os.getpid()}

    bootstrap_response = client.get("/api/system/bootstrap")
    assert bootstrap_response.status_code == 200
    assert bootstrap_response.json() == {
        "embedded_chat": False,
        "auth_scheme": "jwt-password",
        "admin_setup_required": False,
        "lark_sso_enabled": False,
    }


def test_bootstrap_reports_enabled_lark_sso(
    load_backend,
    hermes_home,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LARK_SSO_ENABLE", "true")
    server = load_backend("aegis.backend.server")
    app = server.create_app()

    with TestClient(app) as test_client:
        response = test_client.get("/api/system/bootstrap")

    assert response.status_code == 200
    assert response.json()["lark_sso_enabled"] is True


def test_missing_bootstrap_secret_requires_manual_setup(
    load_backend,
    hermes_home,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AEGIS_BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    server = load_backend("aegis.backend.server")
    app = server.create_app()

    with TestClient(app) as test_client:
        login_response = test_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong-password-for-test"},
        )
        assert login_response.status_code == 401

        bootstrap_response = test_client.get("/api/system/bootstrap")
        assert bootstrap_response.status_code == 200
        assert bootstrap_response.json()["admin_setup_required"] is True


def test_openapi_includes_bearer_auth_scheme(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200

    schema = response.json()
    assert schema["components"]["securitySchemes"]["bearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": (
            "Paste Aegis JWT access token. Swagger will send "
            "`Authorization: Bearer <token>`."
        ),
    }
    assert schema["security"] == [{"bearerAuth": []}]
