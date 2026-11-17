"""Access tokens stop working when their user signs out, changes password or
is disabled, instead of staying valid until they expire."""

from __future__ import annotations

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.aisoc.conftest import BOOTSTRAP_PASSWORD

JWT_SECRET = "test-jwt-secret-1234567890-abcdef"


def _login(client: TestClient, password: str = BOOTSTRAP_PASSWORD) -> str:
    resp = client.post("/api/auth/login", json={"username": "admin", "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _status(client: TestClient, token: str) -> int:
    return client.get("/api/users", headers={"Authorization": f"Bearer {token}"}).status_code


def test_password_change_revokes_tokens_issued_before_it(test_client):
    old = _login(test_client)

    changed = test_client.put(
        "/api/auth/password",
        headers={"Authorization": f"Bearer {old}"},
        json={"old_password": BOOTSTRAP_PASSWORD, "new_password": "brand-new-password-1"},
    )

    assert changed.status_code == 200
    assert _status(test_client, old) == 401
    assert _status(test_client, _login(test_client, "brand-new-password-1")) == 200


def test_logout_revokes_only_the_presented_token(test_client):
    first, second = _login(test_client), _login(test_client)

    resp = test_client.post("/api/auth/logout", headers={"Authorization": f"Bearer {first}"})

    assert resp.json() == {"logged_out": True}
    assert _status(test_client, first) == 401
    assert _status(test_client, second) == 200
    with pytest.raises(WebSocketDisconnect) as closed:
        with test_client.websocket_connect(f"/api/chat/session?token={first}"):
            pass
    assert closed.value.code == 4401


def test_logout_with_a_token_minted_before_jti_signs_the_user_out_everywhere(test_client):
    # Hand-minted shape of a pre-revocation token: no ``ver`` and no ``jti``.
    legacy = jwt.encode({"sub": "0000000000000001", "exp": 4102444800}, JWT_SECRET, algorithm="HS256")
    current = _login(test_client)
    assert _status(test_client, legacy) == 200

    test_client.post("/api/auth/logout", headers={"Authorization": f"Bearer {legacy}"})

    assert _status(test_client, legacy) == 401
    assert _status(test_client, current) == 401
    assert _status(test_client, _login(test_client)) == 200


def test_tokens_issued_before_a_disable_stay_dead_after_re_enabling(test_client, auth_headers):
    uid = test_client.post(
        "/api/users",
        headers=auth_headers,
        json={"username": "analyst", "password": "analyst-pass-1", "email": "a@example.com", "status": "enabled"},
    ).json()["uid"]
    token = test_client.post(
        "/api/auth/login", json={"username": "analyst", "password": "analyst-pass-1"}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert test_client.get("/api/auth/session", headers=headers).json()["authenticated"] is True

    for status in ("disabled", "enabled"):
        test_client.put(f"/api/users/{uid}/status", headers=auth_headers, json={"status": status})

    assert test_client.get("/api/auth/session", headers=headers).json()["authenticated"] is False
