from __future__ import annotations

import os

from fastapi.testclient import TestClient
import pytest

import hermes_self_restart


@pytest.fixture(autouse=True)
def reset_restart_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hermes_self_restart, "_restart_requested", False)


def _create_non_admin_headers(
    client: TestClient,
    auth_headers: dict[str, str],
) -> dict[str, str]:
    created = client.post(
        "/api/users",
        headers=auth_headers,
        json={
            "username": "operator",
            "password": "Password123!",
            "email": "operator@example.com",
            "status": "enabled",
        },
    )
    assert created.status_code == 201
    login = client.post(
        "/api/auth/login",
        json={"username": "operator", "password": "Password123!"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_restart_requires_authentication(client: TestClient) -> None:
    response = client.post("/api/system/restart")

    assert response.status_code == 401


def test_restart_rejects_authenticated_non_admin(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    user_headers = _create_non_admin_headers(client, auth_headers)

    response = client.post("/api/system/restart", headers=user_headers)

    assert response.status_code == 403
    assert response.json() == {"detail": "Admin access required."}


def test_health_is_public_and_exposes_current_process_pid(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "pid": os.getpid()}


def test_graceful_shutdown_interrupts_python_main_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    from aegis.backend.routes import system

    interrupts: list[str] = []
    monkeypatch.setattr(system, "_interrupt_main", lambda: interrupts.append("main"), raising=False)
    if hasattr(system, "os"):
        monkeypatch.setattr(
            system.os,
            "kill",
            lambda *_args: (_ for _ in ()).throw(
                AssertionError("graceful shutdown must not terminate the process directly")
            ),
        )

    system._graceful_shutdown()

    assert interrupts == ["main"]


def test_admin_restart_returns_unified_payload_after_response_is_sent(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aegis.backend.routes import system

    launched: list[object] = []
    response_complete = False
    shutdown_boundaries: list[bool] = []
    monkeypatch.setattr(
        hermes_self_restart,
        "_start_detached_watcher",
        lambda spec: launched.append(spec),
    )
    monkeypatch.setattr(
        system,
        "_graceful_shutdown",
        lambda: shutdown_boundaries.append(response_complete),
        raising=False,
    )

    class ResponseBoundaryApp:
        async def __call__(self, scope, receive, send):
            async def send_with_boundary(message):
                nonlocal response_complete
                await send(message)
                if message["type"] == "http.response.body" and not message.get("more_body", False):
                    response_complete = True

            await client.app(scope, receive, send_with_boundary)

    with TestClient(ResponseBoundaryApp()) as boundary_client:
        response = boundary_client.post("/api/system/restart", headers=auth_headers)

    assert response.status_code == 202
    assert response.json() == {
        "accepted": True,
        "already_requested": False,
        "service": "aegis",
        "pid": os.getpid(),
    }
    assert len(launched) == 1
    assert shutdown_boundaries == [True]


def test_duplicate_restart_returns_202_without_second_watcher(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aegis.backend.routes import system

    launched: list[object] = []
    shutdowns: list[str] = []
    monkeypatch.setattr(
        hermes_self_restart,
        "_start_detached_watcher",
        lambda spec: launched.append(spec),
    )
    monkeypatch.setattr(
        system,
        "_graceful_shutdown",
        lambda: shutdowns.append("aegis"),
        raising=False,
    )

    first = client.post("/api/system/restart", headers=auth_headers)
    duplicate = client.post("/api/system/restart", headers=auth_headers)

    assert first.status_code == 202
    assert duplicate.status_code == 202
    assert duplicate.json() == {
        "accepted": False,
        "already_requested": True,
        "service": "aegis",
        "pid": os.getpid(),
    }
    assert len(launched) == 1
    assert shutdowns == ["aegis"]
