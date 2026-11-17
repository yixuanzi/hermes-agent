"""A2A CORS preflights are answered before Bearer auth; real requests still need the Bearer.

The auth middleware used to wrap ``CORSMiddleware``, so with ``WORKAGENT_A2A_AUTH=true`` a
browser's ``OPTIONS /a2a`` got a bare 401 and every cross-origin call failed at the preflight.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from workagent.backend.a2a_server import A2A_RPC_PATH, create_a2a_app
from workagent.backend.config import WorkagentSettings

_ORIGIN = "http://console.example"


def _client() -> TestClient:
    settings = WorkagentSettings(
        host="testserver",
        a2a_auth_enabled=True,
        a2a_session_token="a2a-secret",
        a2a_token_source="env",
    )
    return TestClient(create_a2a_app(settings))


def test_preflight_succeeds_without_bearer_when_auth_is_on() -> None:
    with _client() as client:
        response = client.options(
            A2A_RPC_PATH,
            headers={
                "Origin": _ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization, content-type, a2a-version",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] in {"*", _ORIGIN}
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_cross_origin_rpc_without_bearer_is_still_rejected() -> None:
    with _client() as client:
        unauthenticated = client.post(
            A2A_RPC_PATH, headers={"Origin": _ORIGIN, "A2A-Version": "1.0"}, json={}
        )
        plain_options = client.options(A2A_RPC_PATH, headers={"Origin": _ORIGIN})

    assert unauthenticated.status_code == 401
    # The browser can read why it was refused instead of reporting an opaque CORS error.
    assert unauthenticated.headers["access-control-allow-origin"] in {"*", _ORIGIN}
    assert plain_options.status_code == 401
