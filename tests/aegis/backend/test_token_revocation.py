"""Aegis access tokens can be revoked.

Logout was client-side only and a JWT stayed valid until ``exp`` (8 h) through logout, password
change, admin reset, demotion and disable. Each token now carries the user's ``token_version``
(``ver``) and a ``jti``: the version bumps on those account events, and logout revokes the one
token it was given. Tokens minted without the claims keep working while the version is 0.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import jwt
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.aegis.backend.conftest import BOOTSTRAP_PASSWORD
from tests.aegis.backend.test_chat_ws import AUTH_TOKEN


_SECRET = "test-jwt-secret-1234567890-abcdef"


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _accepted(client: TestClient, token: str) -> bool:
    status = client.get("/api/prompt-templates", headers=_bearer(token)).status_code
    assert status in {200, 401}
    return status == 200


def _ws_accepted(client: TestClient, token: str) -> bool:
    try:
        with client.websocket_connect(f"/api/chat/ws?token={token}") as ws:
            ws.send_json({"type": "session.resume"})
            ws.receive_json()
    except WebSocketDisconnect as exc:
        assert exc.code == 4401
        return False
    return True


def _create_user(client: TestClient, admin: dict[str, str], username: str, role: str) -> str:
    response = client.post(
        "/api/users",
        headers=admin,
        json={
            "username": username,
            "password": "Password123!",
            "email": f"{username}@example.com",
            "status": "enabled",
            "role": role,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["uid"]


def test_logout_revokes_only_the_presented_token_over_http_and_ws(client: TestClient) -> None:
    first = _login(client, "admin", BOOTSTRAP_PASSWORD)
    second = _login(client, "admin", BOOTSTRAP_PASSWORD)

    assert client.post("/api/auth/logout", headers=_bearer(first)).json() == {"logged_out": True}

    assert not _accepted(client, first)
    assert not _ws_accepted(client, first)
    assert _accepted(client, second)
    assert _ws_accepted(client, second)


def test_password_change_revokes_every_token_of_the_user(client: TestClient) -> None:
    first = _login(client, "admin", BOOTSTRAP_PASSWORD)
    second = _login(client, "admin", BOOTSTRAP_PASSWORD)

    changed = client.put(
        "/api/auth/password",
        headers=_bearer(first),
        json={"old_password": BOOTSTRAP_PASSWORD, "new_password": "AdminPassword123!"},
    )

    assert changed.status_code == 200
    assert not _accepted(client, first)
    assert not _accepted(client, second)
    assert _accepted(client, _login(client, "admin", "AdminPassword123!"))


def test_admin_reset_demotion_and_disable_revoke_but_promotion_does_not(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    uid = _create_user(client, auth_headers, "analyst", "operator")

    def _change(path: str, body: dict) -> None:
        assert client.put(f"/api/users/{uid}/{path}", headers=auth_headers, json=body).status_code == 200

    token = _login(client, "analyst", "Password123!")
    _change("role", {"role": "admin"})
    assert _accepted(client, token)
    _change("role", {"role": "user"})
    assert not _accepted(client, token)

    token = _login(client, "analyst", "Password123!")
    _change("status", {"status": "disabled"})
    _change("status", {"status": "enabled"})
    assert not _accepted(client, token)

    token = _login(client, "analyst", "Password123!")
    _change("password", {"password": "NewPassword123!"})
    assert not _accepted(client, token)
    assert _accepted(client, _login(client, "analyst", "NewPassword123!"))


def test_tokens_without_claims_stay_valid_until_a_legacy_logout_bumps_the_version(
    client: TestClient,
) -> None:
    minted_with_claims = _login(client, "admin", BOOTSTRAP_PASSWORD)
    assert _accepted(client, AUTH_TOKEN)

    client.post("/api/auth/logout", headers=_bearer(AUTH_TOKEN))

    assert not _accepted(client, AUTH_TOKEN)
    assert not _accepted(client, minted_with_claims)
    assert _accepted(client, _login(client, "admin", BOOTSTRAP_PASSWORD))


def test_sso_ticket_exchange_mints_a_revocable_token(client: TestClient) -> None:
    from aegis.backend.services.oidc_service import create_sso_login_ticket

    user_service = client.app.state.user_service
    ticket = create_sso_login_ticket(user_service.store, user_service.get_user_by_uid("0000000000000001"))
    client.cookies.set("aegis_sso_ticket", ticket)
    exchanged = client.post("/api/sso/exchange")
    client.cookies.clear()
    token = exchanged.json()["access_token"]

    claims = jwt.decode(token, _SECRET, algorithms=["HS256"])
    assert claims["ver"] == 0 and claims["jti"]
    client.post("/api/auth/logout", headers=_bearer(token))
    assert not _accepted(client, token)


def test_existing_database_gains_token_version_and_keeps_old_tokens_valid(
    load_backend,
    hermes_home: Path,
) -> None:
    with sqlite3.connect(hermes_home / "aegis.db") as connection:
        connection.execute(
            "CREATE TABLE users (uid TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
            "passwd TEXT NOT NULL, email TEXT UNIQUE NOT NULL, oidc_subject TEXT UNIQUE, "
            "status TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user', "
            "create_time TEXT NOT NULL, last_login TEXT)"
        )
        connection.execute(
            "INSERT INTO users(uid, username, passwd, email, status, role, create_time) VALUES "
            "('0000000000000001', 'admin', 'hash', 'admin@aegis.local', 'enabled', 'admin', '2026-01-01T00:00:00Z')"
        )
    server = load_backend("aegis.backend.server")

    with TestClient(server.create_app()) as client:
        assert _accepted(client, AUTH_TOKEN)


def test_revoked_token_rows_are_pruned_once_expired(tmp_path: Path) -> None:
    from aegis.backend.services.user_store import AegisUserStore

    store = AegisUserStore(tmp_path / "aegis.db")
    store.revoke_access_token("expired", expires_at=100, now=50)
    store.revoke_access_token("live", expires_at=1_000, now=200)

    with sqlite3.connect(tmp_path / "aegis.db") as connection:
        rows = {row[0] for row in connection.execute("SELECT jti FROM revoked_access_tokens")}
    assert rows == {"live"}
    assert store.is_access_token_revoked("live")

