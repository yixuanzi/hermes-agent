from __future__ import annotations

import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

from tests.aisoc.conftest import BOOTSTRAP_PASSWORD


def test_user_store_migrates_pre_display_name_database(tmp_path) -> None:
    """Regression guard: opening a database created before display_name existed
    must not crash, and old rows should read back with display_name=None."""
    from aisoc.backend.services.user_store import AisocUserStore

    db_path = tmp_path / "aisoc.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE users (
            uid TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            passwd TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('enabled', 'disabled')),
            create_time TEXT NOT NULL,
            last_login TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO users (uid, username, passwd, email, status, create_time) "
        "VALUES ('u1', 'legacy', 'hash', 'legacy@example.com', 'enabled', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    store = AisocUserStore(path=db_path)
    row = store.get_user_by_username("legacy")
    assert row is not None
    assert row["display_name"] is None

    store.update_display_name("u1", "Legacy User")
    assert store.get_user_by_uid("u1")["display_name"] == "Legacy User"


def test_load_settings_generates_jwt_secret_when_env_missing(monkeypatch) -> None:
    monkeypatch.delenv("AISOC_JWT_SECRET", raising=False)

    from aisoc.backend.config import load_aisoc_settings

    settings = load_aisoc_settings()
    assert settings.jwt_secret
    assert settings.jwt_expire_seconds == 28800


def test_login_session_and_logout_routes(test_client: TestClient) -> None:
    login_response = test_client.post(
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

    session_without_auth = test_client.get("/api/auth/session")
    assert session_without_auth.status_code == 200
    assert session_without_auth.json() == {"authenticated": False}

    session_with_auth = test_client.get(
        "/api/auth/session",
        headers={"Authorization": f"Bearer {payload['access_token']}"},
    )
    assert session_with_auth.status_code == 200
    session_payload = session_with_auth.json()
    assert session_payload["authenticated"] is True
    assert session_payload["user"]["username"] == "admin"
    assert session_payload["expires_in"] > 0

    logout_response = test_client.post("/api/auth/logout")
    assert logout_response.status_code == 200
    assert logout_response.json() == {"logged_out": True}


def test_login_rejects_bad_password(test_client: TestClient) -> None:
    resp = test_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrong-password"},
    )
    assert resp.status_code == 401


def test_register_defaults_user_to_disabled_and_login_is_blocked(test_client: TestClient) -> None:
    register_response = test_client.post(
        "/api/auth/register",
        json={
            "username": "new-user",
            "password": "Password123!",
            "email": "new-user@example.com",
        },
    )
    assert register_response.status_code == 201
    assert register_response.json() == {"registered": True, "status": "disabled"}

    login_response = test_client.post(
        "/api/auth/login",
        json={"username": "new-user", "password": "Password123!"},
    )
    assert login_response.status_code == 403
    assert login_response.json() == {"detail": "User account is disabled."}


def test_change_password_updates_user_credentials(
    test_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    change_response = test_client.put(
        "/api/auth/password",
        headers=auth_headers,
        json={"old_password": BOOTSTRAP_PASSWORD, "new_password": "AdminPassword123!"},
    )
    assert change_response.status_code == 200
    assert change_response.json() == {"updated": True}

    old_login = test_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": BOOTSTRAP_PASSWORD},
    )
    assert old_login.status_code == 401

    new_login = test_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "AdminPassword123!"},
    )
    assert new_login.status_code == 200


def test_update_profile_changes_display_name(
    test_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    session_before = test_client.get("/api/auth/session", headers=auth_headers)
    assert session_before.json()["user"]["display_name"] == "admin"

    update_response = test_client.put(
        "/api/auth/profile",
        headers=auth_headers,
        json={"display_name": "Ada"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["display_name"] == "Ada"

    session_after = test_client.get("/api/auth/session", headers=auth_headers)
    assert session_after.json()["user"]["display_name"] == "Ada"


def test_update_profile_rejects_blank_display_name(
    test_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    resp = test_client.put(
        "/api/auth/profile",
        headers=auth_headers,
        json={"display_name": "   "},
    )
    assert resp.status_code == 422


def test_middleware_blocks_protected_api_without_token(test_client: TestClient) -> None:
    unauthorized = test_client.get("/api/does-not-exist")
    assert unauthorized.status_code == 401
    assert unauthorized.json() == {"detail": "Unauthorized"}


def test_old_shared_token_no_longer_grants_access(test_client: TestClient) -> None:
    """Regression guard: the retired AISOC_SESSION_TOKEN model must not work anymore."""
    resp = test_client.get(
        "/api/overview/status",
        headers={"Authorization": "Bearer some-old-shared-secret-value"},
    )
    assert resp.status_code == 401


def test_openapi_exposes_bearer_auth_scheme(test_client: TestClient) -> None:
    resp = test_client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    security_schemes = schema["components"]["securitySchemes"]
    assert security_schemes["bearerAuth"]["type"] == "http"
    assert security_schemes["bearerAuth"]["scheme"] == "bearer"
    assert security_schemes["bearerAuth"]["bearerFormat"] == "JWT"
    assert {"bearerAuth": []} in schema.get("security", [])


def test_public_health_and_bootstrap_routes_are_accessible(test_client: TestClient) -> None:
    health_response = test_client.get("/health")
    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok", "pid": os.getpid()}

    bootstrap_response = test_client.get("/api/system/bootstrap")
    assert bootstrap_response.status_code == 200
    assert bootstrap_response.json() == {
        "auth_scheme": "jwt-password",
        "admin_setup_required": False,
    }


def test_missing_bootstrap_secret_requires_manual_setup(monkeypatch) -> None:
    monkeypatch.delenv("AISOC_BOOTSTRAP_ADMIN_PASSWORD", raising=False)

    import aisoc.backend.services.user_store as user_store_module
    from aisoc.backend.config import load_aisoc_settings
    from aisoc.backend.server import create_app

    # The user store is a process-wide singleton; reset it so this test's
    # fresh HERMES_HOME tempdir doesn't inherit an admin seeded by an
    # earlier test in this module.
    monkeypatch.setattr(user_store_module, "_STORE", None)

    app = create_app(load_aisoc_settings())
    with TestClient(app) as client:
        login_response = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong-password-for-test"},
        )
        assert login_response.status_code == 401

        bootstrap_response = client.get("/api/system/bootstrap")
        assert bootstrap_response.status_code == 200
        assert bootstrap_response.json()["admin_setup_required"] is True
