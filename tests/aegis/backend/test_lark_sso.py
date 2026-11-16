from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _FakeLarkClient:
    def __init__(self, *, email: str = "lark@example.com"):
        self.email = email
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return _FakeResponse(
            {
                "code": "0",
                "access_token": "lark-user-access-token",
                "token_type": "Bearer",
                "expires_in": 7200,
            }
        )

    async def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        return _FakeResponse(
            {
                "code": 0,
                "msg": "success",
                "data": {"email": self.email},
            }
        )


@pytest.fixture
def lark_client(monkeypatch, load_backend, hermes_home):
    monkeypatch.setenv("LARK_SSO_ENABLE", "true")
    monkeypatch.setenv("LARK_APP_ID", "cli-lark-test")
    monkeypatch.setenv("LARK_APP_SECRET", "lark-test-secret")
    monkeypatch.setenv("LARK_REDIRECT_URI", "http://127.0.0.1:9130/api/lark/callback")
    server = load_backend("aegis.backend.server")
    app = server.create_app()
    with TestClient(app) as client:
        yield client, app


def _start_lark(client: TestClient) -> tuple[str, dict[str, list[str]]]:
    response = client.get("/api/lark/start", follow_redirects=False)
    assert response.status_code == 302
    parsed = urlparse(response.headers["location"])
    return parsed.path, parse_qs(parsed.query)


def test_lark_start_builds_authorize_url_with_state_and_pkce(lark_client):
    client, app = lark_client

    path, query = _start_lark(client)

    assert path == "/open-apis/authen/v1/authorize"
    assert query["client_id"] == ["cli-lark-test"]
    assert query["response_type"] == ["code"]
    assert query["redirect_uri"] == ["http://127.0.0.1:9130/api/lark/callback"]
    assert query["scope"] == ["contact:user.email:readonly"]
    assert len(query["state"][0]) > 20
    assert len(query["code_challenge"][0]) > 20
    assert query["code_challenge_method"] == ["S256"]

    transaction = app.state.user_service.store.get_lark_login_transaction(
        query["state"][0],
        "0000-01-01T00:00:00Z",
    )
    assert transaction is not None
    assert transaction["app_id"] == "cli-lark-test"
    assert transaction["code_verifier"]


def test_lark_start_requires_credentials(load_backend, hermes_home, monkeypatch):
    monkeypatch.setenv("LARK_SSO_ENABLE", "true")
    monkeypatch.delenv("LARK_APP_ID", raising=False)
    monkeypatch.delenv("LARK_APP_SECRET", raising=False)
    server = load_backend("aegis.backend.server")
    app = server.create_app()

    with TestClient(app) as client:
        response = client.get("/api/lark/start")

    assert response.status_code == 503
    assert response.json() == {"detail": "Lark SSO is not configured."}


def test_lark_start_requires_enable_flag(load_backend, hermes_home, monkeypatch):
    monkeypatch.delenv("LARK_SSO_ENABLE", raising=False)
    monkeypatch.setenv("LARK_APP_ID", "cli-lark-test")
    monkeypatch.setenv("LARK_APP_SECRET", "lark-test-secret")
    server = load_backend("aegis.backend.server")
    app = server.create_app()

    with TestClient(app) as client:
        response = client.get("/api/lark/start")

    assert response.status_code == 503
    assert response.json() == {"detail": "Lark SSO is disabled."}


def test_lark_callback_exchanges_code_gets_email_and_reuses_ticket(lark_client, monkeypatch):
    client, app = lark_client
    _path, query = _start_lark(client)
    state = query["state"][0]
    transaction = app.state.user_service.store.get_lark_login_transaction(
        state,
        "0000-01-01T00:00:00Z",
    )
    assert transaction is not None
    fake_client = _FakeLarkClient()
    module = __import__("aegis.backend.services.lark_service", fromlist=["httpx"])
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_kwargs: fake_client)

    callback = client.get(
        f"/api/lark/callback?code=lark-code&state={state}",
        follow_redirects=False,
    )

    assert callback.status_code == 302
    assert callback.headers["location"] == "/sso/callback"
    assert "aegis_sso_ticket=" in callback.headers["set-cookie"]
    assert 'aegis_lark_state=""' in callback.headers["set-cookie"]
    assert fake_client.posts[0][0].endswith("/open-apis/authen/v2/oauth/token")
    token_body = fake_client.posts[0][1]["json"]
    assert token_body["grant_type"] == "authorization_code"
    assert token_body["client_id"] == "cli-lark-test"
    assert token_body["client_secret"] == "lark-test-secret"
    assert token_body["code"] == "lark-code"
    assert token_body["redirect_uri"] == "http://127.0.0.1:9130/api/lark/callback"
    assert token_body["code_verifier"] == transaction["code_verifier"]
    assert fake_client.gets == [
        (
            "https://open.larksuite.com/open-apis/authen/v1/user_info",
            {"headers": {"Authorization": "Bearer lark-user-access-token"}},
        )
    ]

    exchange = client.post("/api/sso/exchange")
    assert exchange.status_code == 200
    payload = exchange.json()
    assert payload["authenticated"] is True
    assert payload["user"]["email"] == "lark@example.com"
    assert payload["user"]["username"].startswith("lark_")
    assert "lark-user-access-token" not in exchange.text

    replay = client.post("/api/sso/exchange")
    assert replay.status_code == 401

    assert app.state.user_service.store.get_user_by_email("lark@example.com") is not None


def test_lark_existing_email_reuses_local_user(lark_client):
    _client, app = lark_client
    service = app.state.user_service
    first = service.upsert_lark_user(email="Existing@Example.com")
    second = service.upsert_lark_user(email="existing@example.com")

    assert second.uid == first.uid
    assert second.email == "existing@example.com"


def test_lark_disabled_user_and_local_admin_cannot_be_bound_by_email(lark_client):
    _client, app = lark_client
    service = app.state.user_service
    service._create_user(
        username="disabled-user",
        password="Password123!",
        email="disabled@example.com",
        status="disabled",
    )

    with pytest.raises(HTTPException, match="disabled"):
        service.upsert_lark_user(email="disabled@example.com")
    with pytest.raises(HTTPException, match="admin"):
        service.upsert_lark_user(email="admin@aegis.local")


def test_lark_callback_rejects_missing_email(lark_client, monkeypatch):
    client, _app = lark_client
    _path, query = _start_lark(client)

    class MissingEmailClient(_FakeLarkClient):
        async def get(self, url, **kwargs):
            self.gets.append((url, kwargs))
            return _FakeResponse({"code": 0, "data": {}})

    module = __import__("aegis.backend.services.lark_service", fromlist=["httpx"])
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_kwargs: MissingEmailClient())

    response = client.get(
        f"/api/lark/callback?code=lark-code&state={query['state'][0]}",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert parse_qs(urlparse(response.headers["location"]).query)["error_description"] == [
        "Lark user email is missing."
    ]


def test_lark_callback_rejects_invalid_state_without_calling_lark(lark_client, monkeypatch):
    client, _app = lark_client
    fake_client = _FakeLarkClient()
    module = __import__("aegis.backend.services.lark_service", fromlist=["httpx"])
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_kwargs: fake_client)

    response = client.get(
        "/api/lark/callback?code=lark-code&state=invalid-state",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert parse_qs(urlparse(response.headers["location"]).query)["error_description"] == [
        "Lark login state is invalid or expired."
    ]
    assert fake_client.posts == []


def test_lark_callback_validates_and_consumes_state_on_authorization_denial(lark_client):
    client, app = lark_client
    _path, query = _start_lark(client)
    state = query["state"][0]

    response = client.get(
        f"/api/lark/callback?error=access_denied&state={state}",
        follow_redirects=False,
    )

    assert response.status_code == 302
    callback_error = parse_qs(urlparse(response.headers["location"]).query)
    assert callback_error["error"] == ["access_denied"]
    assert app.state.user_service.store.get_lark_login_transaction(state, "9999-12-31T00:00:00Z") is None

    replay = client.get(
        f"/api/lark/callback?code=lark-code&state={state}",
        follow_redirects=False,
    )
    replay_error = parse_qs(urlparse(replay.headers["location"]).query)
    assert replay_error["error_description"] == ["Lark login state is invalid or expired."]


def test_lark_callback_rejects_authorization_denial_with_invalid_state(lark_client):
    client, _app = lark_client

    response = client.get(
        "/api/lark/callback?error=access_denied&state=invalid-state",
        follow_redirects=False,
    )

    assert response.status_code == 302
    callback_error = parse_qs(urlparse(response.headers["location"]).query)
    assert callback_error["error"] == ["invalid_request"]
    assert callback_error["error_description"] == ["Lark login state is invalid or expired."]


def test_lark_callback_rejects_expired_state_without_calling_lark(lark_client, monkeypatch):
    client, app = lark_client
    _path, query = _start_lark(client)
    state = query["state"][0]
    store = app.state.user_service.store
    with store._connect() as conn:
        conn.execute(
            "UPDATE lark_login_transactions SET expires_at = ? WHERE state = ?",
            ("2000-01-01T00:00:00Z", state),
        )
        conn.commit()

    fake_client = _FakeLarkClient()
    module = __import__("aegis.backend.services.lark_service", fromlist=["httpx"])
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_kwargs: fake_client)

    response = client.get(
        f"/api/lark/callback?code=lark-code&state={state}",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert parse_qs(urlparse(response.headers["location"]).query)["error_description"] == [
        "Lark login transaction is invalid or expired."
    ]
    assert fake_client.posts == []


def test_lark_callback_rejects_token_upstream_error(lark_client, monkeypatch):
    client, _app = lark_client
    _path, query = _start_lark(client)

    class TokenErrorClient(_FakeLarkClient):
        async def post(self, url, **kwargs):
            self.posts.append((url, kwargs))
            return _FakeResponse({"code": 99991663, "msg": "invalid code"})

    fake_client = TokenErrorClient()
    module = __import__("aegis.backend.services.lark_service", fromlist=["httpx"])
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_kwargs: fake_client)

    response = client.get(
        f"/api/lark/callback?code=lark-code&state={query['state'][0]}",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert parse_qs(urlparse(response.headers["location"]).query)["error_description"] == [
        "Lark token exchange failed."
    ]


def test_lark_callback_rejects_user_info_upstream_error(lark_client, monkeypatch):
    client, _app = lark_client
    _path, query = _start_lark(client)

    class UserInfoErrorClient(_FakeLarkClient):
        async def get(self, url, **kwargs):
            self.gets.append((url, kwargs))
            return _FakeResponse({"code": 99991663, "msg": "invalid token"})

    fake_client = UserInfoErrorClient()
    module = __import__("aegis.backend.services.lark_service", fromlist=["httpx"])
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_kwargs: fake_client)

    response = client.get(
        f"/api/lark/callback?code=lark-code&state={query['state'][0]}",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert parse_qs(urlparse(response.headers["location"]).query)["error_description"] == [
        "Lark user information request failed."
    ]


def test_lark_start_rejects_external_post_login_redirect(load_backend, hermes_home, monkeypatch):
    monkeypatch.setenv("LARK_SSO_ENABLE", "true")
    monkeypatch.setenv("LARK_APP_ID", "cli-lark-test")
    monkeypatch.setenv("LARK_APP_SECRET", "lark-test-secret")
    monkeypatch.setenv("OIDC_POST_LOGIN_REDIRECT", "//evil.example/capture")
    server = load_backend("aegis.backend.server")
    app = server.create_app()

    with TestClient(app) as client:
        response = client.get("/api/lark/start", follow_redirects=False)

    assert response.status_code == 500
    assert response.json() == {"detail": "Post-login redirect must be a local path."}
