from __future__ import annotations

import os

from fastapi.testclient import TestClient
import pytest

import hermes_self_restart
import aisoc.backend.services.user_store as _user_store_module
from aisoc.backend.config import load_aisoc_settings
from aisoc.backend.server import create_app


BOOTSTRAP_PASSWORD = "test-admin-password-123"


@pytest.fixture(autouse=True)
def reset_restart_guard(monkeypatch) -> None:
    monkeypatch.setattr(hermes_self_restart, "_restart_requested", False)


def _client(monkeypatch) -> tuple[TestClient, dict[str, str]]:
    monkeypatch.setenv("AISOC_BOOTSTRAP_ADMIN_PASSWORD", BOOTSTRAP_PASSWORD)
    monkeypatch.setenv("AISOC_JWT_SECRET", "test-jwt-secret-1234567890-abcdef")
    monkeypatch.setattr(_user_store_module, "_STORE", None)
    settings = load_aisoc_settings()
    client = TestClient(create_app(settings))
    login = client.post(
        "/api/auth/login", json={"username": "admin", "password": BOOTSTRAP_PASSWORD}
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def test_restart_requires_valid_bearer_auth(monkeypatch) -> None:
    launched = []
    monkeypatch.setattr(
        hermes_self_restart,
        "_start_detached_watcher",
        lambda spec: launched.append(spec),
    )
    client, _headers = _client(monkeypatch)

    missing = client.post("/api/system/restart")
    invalid = client.post(
        "/api/system/restart",
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert launched == []


def test_health_is_public_and_exposes_current_process_pid(monkeypatch) -> None:
    client, _headers = _client(monkeypatch)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "pid": os.getpid()}


def test_graceful_shutdown_interrupts_python_main_thread(monkeypatch) -> None:
    from aisoc.backend.routes import system

    interrupts = []
    monkeypatch.setattr(
        system,
        "_interrupt_main",
        lambda: interrupts.append("main"),
        raising=False,
    )
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


def test_restart_returns_accepted_payload_and_defers_shutdown(
    monkeypatch,
) -> None:
    from aisoc.backend.routes import system

    launched = []
    shutdowns = []
    monkeypatch.setattr(
        hermes_self_restart,
        "_start_detached_watcher",
        lambda spec: launched.append(spec),
    )
    monkeypatch.setattr(
        system,
        "_graceful_shutdown",
        lambda: shutdowns.append("aisoc"),
        raising=False,
    )
    client, headers = _client(monkeypatch)

    response = client.post("/api/system/restart", headers=headers)

    assert response.status_code == 202
    assert response.json() == {
        "accepted": True,
        "already_requested": False,
        "service": "aisoc",
        "pid": os.getpid(),
    }
    assert len(launched) == 1
    assert shutdowns == ["aisoc"]


def test_duplicate_restart_returns_202_without_second_watcher(
    monkeypatch,
) -> None:
    from aisoc.backend.routes import system

    launched = []
    shutdowns = []
    monkeypatch.setattr(
        hermes_self_restart,
        "_start_detached_watcher",
        lambda spec: launched.append(spec),
    )
    monkeypatch.setattr(
        system,
        "_graceful_shutdown",
        lambda: shutdowns.append("aisoc"),
        raising=False,
    )
    client, headers = _client(monkeypatch)

    first = client.post("/api/system/restart", headers=headers)
    duplicate = client.post("/api/system/restart", headers=headers)

    assert first.status_code == 202
    assert duplicate.status_code == 202
    assert duplicate.json() == {
        "accepted": False,
        "already_requested": True,
        "service": "aisoc",
        "pid": os.getpid(),
    }
    assert len(launched) == 1
    assert shutdowns == ["aisoc"]
