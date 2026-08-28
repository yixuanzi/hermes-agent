from __future__ import annotations

import json
import sqlite3
from pathlib import Path
import time
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from aisoc.backend.services.user_store import AisocUserStore


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _FakeOidcClient:
    def __init__(self, *, token_payload: dict, jwks_payload: dict, userinfo_payload: dict):
        self.token_payload = token_payload
        self.jwks_payload = jwks_payload
        self.userinfo_payload = userinfo_payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, _url, **_kwargs):
        return _FakeResponse(self.token_payload)

    async def get(self, url, **_kwargs):
        if url.endswith("jwks.json"):
            return _FakeResponse(self.jwks_payload)
        return _FakeResponse(self.userinfo_payload)


def _signed_token(issuer: str, client_id: str, subject: str, organization_id: str, nonce: str):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    claims = {
        "iss": issuer,
        "sub": subject,
        "aud": client_id,
        "email": "sso@example.com",
        "organization_id": organization_id,
        "organization_name": "Example Org",
        "nonce": nonce,
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
    }
    token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})
    return token, {"keys": [{**public_jwk, "kid": "test-key", "alg": "RS256", "use": "sig"}]}


def test_existing_users_database_is_migrated_without_recreating_users(tmp_path):
    database_path = Path(tmp_path) / "aisoc.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE users (uid TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
            "passwd TEXT NOT NULL, email TEXT UNIQUE NOT NULL, status TEXT NOT NULL, "
            "create_time TEXT NOT NULL, last_login TEXT)"
        )
        connection.execute(
            "INSERT INTO users(uid, username, passwd, email, status, create_time) "
            "VALUES ('legacy-uid', 'legacy', 'hash', 'legacy@example.com', 'enabled', '2026-01-01T00:00:00Z')"
        )
        connection.commit()

    store = AisocUserStore(database_path)

    assert store.get_user_by_uid("legacy-uid")["username"] == "legacy"
    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "oidc_subject" in columns
    assert {"oidc_login_transactions", "sso_login_tickets"}.issubset(tables)


@pytest.fixture
def oidc_client(monkeypatch, load_backend, hermes_home):
    monkeypatch.setenv("AISOC_OIDC_ISSUER", "http://127.0.0.1:8080")
    monkeypatch.setenv("AISOC_OIDC_BACKCHANNEL_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("AISOC_OIDC_CLIENT_ID", "aisoc-test-client")
    monkeypatch.setenv("AISOC_OIDC_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("AISOC_OIDC_REDIRECT_URI", "http://127.0.0.1:9120/api/sso/callback")
    server = load_backend("aisoc.backend.server")
    app = server.create_app()
    with TestClient(app) as client:
        yield client, app


def test_sso_start_uses_client_id_and_sso_without_subscription(oidc_client):
    client, _app = oidc_client

    response = client.get("/api/sso/start?sso=1", follow_redirects=False)

    assert response.status_code == 302
    location = response.headers["location"]
    query = parse_qs(urlparse(location).query)
    assert query["client_id"] == ["aisoc-test-client"]
    assert query["sso"] == ["1"]
    assert "organization_id" not in query
    assert "subscription_id" not in query
    assert query["code_challenge_method"] == ["S256"]


def test_direct_start_rejects_wrong_client_id_and_preserves_only_org_context(oidc_client):
    client, _app = oidc_client

    wrong = client.get(
        "/api/sso/start?organization_id=org-1&client_id=wrong-client",
        follow_redirects=False,
    )
    assert wrong.status_code == 400

    response = client.get(
        "/api/sso/start?organization_id=org-1&client_id=aisoc-test-client&subscription_id=secret",
        follow_redirects=False,
    )
    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["organization_id"] == ["org-1"]
    assert query["client_id"] == ["aisoc-test-client"]
    assert "subscription_id" not in query
    assert "sso" not in query


def test_start_returns_503_when_oidc_not_configured(load_backend, hermes_home):
    server = load_backend("aisoc.backend.server")
    app = server.create_app()
    with TestClient(app) as client:
        response = client.get("/api/sso/start?sso=1", follow_redirects=False)
    assert response.status_code == 503


def test_callback_validates_oidc_and_exchanges_ticket_once(oidc_client, monkeypatch):
    client, app = oidc_client
    start = client.get(
        "/api/sso/start?organization_id=org-1&client_id=aisoc-test-client",
        follow_redirects=False,
    )
    authorize_query = parse_qs(urlparse(start.headers["location"]).query)
    state = authorize_query["state"][0]
    transaction = app.state.user_service.store.get_oidc_login_transaction(state, "0000-01-01T00:00:00Z")
    token, jwks = _signed_token("http://127.0.0.1:8080", "aisoc-test-client", "member-1", "org-1", transaction["nonce"])
    module = __import__("aisoc.backend.services.oidc_service", fromlist=["httpx"])
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **_kwargs: _FakeOidcClient(
            token_payload={"access_token": "portal-access", "id_token": token},
            jwks_payload=jwks,
            userinfo_payload={
                "sub": "member-1",
                "email": "sso@example.com",
                "organization_id": "org-1",
            },
        ),
    )

    callback = client.get(
        f"/api/sso/callback?code=authorization-code&state={state}",
        follow_redirects=False,
    )

    assert callback.status_code == 302
    assert callback.headers["location"] == "/sso/callback"
    assert "authorization-code" not in callback.headers["location"]
    assert "aisoc_sso_ticket=" in callback.headers["set-cookie"]

    exchange = client.post("/api/sso/exchange")
    assert exchange.status_code == 200
    payload = exchange.json()
    assert payload["authenticated"] is True
    assert payload["user"]["email"] == "sso@example.com"
    assert payload["user"]["is_admin"] is False
    assert payload["user"]["username"].startswith("oidc_")

    replay = client.post("/api/sso/exchange")
    assert replay.status_code == 401

    store = app.state.user_service.store
    assert store.get_user_by_oidc_subject("member-1")["email"] == "sso@example.com"


def test_second_login_reuses_the_same_local_account(oidc_client, monkeypatch):
    client, app = oidc_client

    def _run_callback(subject: str, email: str) -> dict:
        start = client.get(
            "/api/sso/start?organization_id=org-1&client_id=aisoc-test-client",
            follow_redirects=False,
        )
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        transaction = app.state.user_service.store.get_oidc_login_transaction(state, "0000-01-01T00:00:00Z")
        token, jwks = _signed_token("http://127.0.0.1:8080", "aisoc-test-client", subject, "org-1", transaction["nonce"])
        module = __import__("aisoc.backend.services.oidc_service", fromlist=["httpx"])
        monkeypatch.setattr(
            module.httpx,
            "AsyncClient",
            lambda **_kwargs: _FakeOidcClient(
                token_payload={"access_token": "portal-access", "id_token": token},
                jwks_payload=jwks,
                userinfo_payload={"sub": subject, "email": email, "organization_id": "org-1"},
            ),
        )
        client.get(f"/api/sso/callback?code=authorization-code&state={state}", follow_redirects=False)
        return client.post("/api/sso/exchange").json()

    first = _run_callback("member-repeat", "repeat@example.com")
    second = _run_callback("member-repeat", "repeat@example.com")

    assert first["user"]["uid"] == second["user"]["uid"]
    assert len(app.state.user_service.store.list_users()) == 2  # bootstrap admin + this OIDC user


def test_callback_rejects_direct_organization_mismatch(oidc_client, monkeypatch):
    client, _app = oidc_client
    start = client.get(
        "/api/sso/start?organization_id=org-1&client_id=aisoc-test-client",
        follow_redirects=False,
    )
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    transaction = _app.state.user_service.store.get_oidc_login_transaction(state, "0000-01-01T00:00:00Z")
    token, jwks = _signed_token("http://127.0.0.1:8080", "aisoc-test-client", "member-2", "org-2", transaction["nonce"])
    module = __import__("aisoc.backend.services.oidc_service", fromlist=["httpx"])
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **_kwargs: _FakeOidcClient(
            token_payload={"access_token": "portal-access", "id_token": token},
            jwks_payload=jwks,
            userinfo_payload={"sub": "member-2", "email": "other@example.com", "organization_id": "org-2"},
        ),
    )

    callback = client.get(
        f"/api/sso/callback?code=authorization-code&state={state}",
        follow_redirects=False,
    )

    assert callback.status_code == 302
    assert parse_qs(urlparse(callback.headers["location"]).query)["error_description"] == [
        "OIDC organization context validation failed."
    ]


def test_callback_rejects_expired_or_reused_state(oidc_client, monkeypatch):
    client, app = oidc_client
    start = client.get(
        "/api/sso/start?organization_id=org-1&client_id=aisoc-test-client",
        follow_redirects=False,
    )
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    transaction = app.state.user_service.store.get_oidc_login_transaction(state, "0000-01-01T00:00:00Z")
    token, jwks = _signed_token("http://127.0.0.1:8080", "aisoc-test-client", "member-3", "org-1", transaction["nonce"])
    module = __import__("aisoc.backend.services.oidc_service", fromlist=["httpx"])
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **_kwargs: _FakeOidcClient(
            token_payload={"access_token": "portal-access", "id_token": token},
            jwks_payload=jwks,
            userinfo_payload={"sub": "member-3", "email": "third@example.com", "organization_id": "org-1"},
        ),
    )

    first = client.get(f"/api/sso/callback?code=authorization-code&state={state}", follow_redirects=False)
    assert first.status_code == 302
    assert first.headers["location"] == "/sso/callback"

    replay = client.get(f"/api/sso/callback?code=authorization-code&state={state}", follow_redirects=False)
    assert parse_qs(urlparse(replay.headers["location"]).query)["error_description"] == [
        "OIDC login transaction is invalid or expired."
    ]


def test_callback_rejects_nonce_mismatch(oidc_client, monkeypatch):
    client, app = oidc_client
    start = client.get(
        "/api/sso/start?organization_id=org-1&client_id=aisoc-test-client",
        follow_redirects=False,
    )
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    token, jwks = _signed_token("http://127.0.0.1:8080", "aisoc-test-client", "member-4", "org-1", "wrong-nonce")
    module = __import__("aisoc.backend.services.oidc_service", fromlist=["httpx"])
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **_kwargs: _FakeOidcClient(
            token_payload={"access_token": "portal-access", "id_token": token},
            jwks_payload=jwks,
            userinfo_payload={"sub": "member-4", "email": "fourth@example.com", "organization_id": "org-1"},
        ),
    )

    callback = client.get(f"/api/sso/callback?code=authorization-code&state={state}", follow_redirects=False)
    assert parse_qs(urlparse(callback.headers["location"]).query)["error_description"] == [
        "ID Token nonce validation failed."
    ]


def test_existing_local_user_binds_by_email_but_admin_does_not(oidc_client, auth_headers):
    client, app = oidc_client
    create = client.post(
        "/api/users",
        headers=auth_headers,
        json={
            "username": "local-user",
            "password": "Password123!",
            "email": "local@example.com",
            "status": "enabled",
        },
    )
    assert create.status_code == 201
    user_uid = create.json()["uid"]
    bound = app.state.user_service.upsert_oidc_user(subject="member-local", email="LOCAL@example.com")
    assert bound.uid == user_uid
    assert app.state.user_service.store.get_user_by_oidc_subject("member-local")["uid"] == user_uid

    with pytest.raises(Exception) as exc_info:
        app.state.user_service.upsert_oidc_user(subject="member-admin", email="admin@aisoc.local")
    assert "admin" in str(exc_info.value).lower()
