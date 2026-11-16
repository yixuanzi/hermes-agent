from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


BOOTSTRAP_PASSWORD = "BootstrapPassword123!"


def _clear_aegis_backend_modules() -> None:
    for module_name in list(sys.modules):
        if module_name == "aegis.backend" or module_name.startswith("aegis.backend."):
            sys.modules.pop(module_name, None)


@pytest.fixture
def load_backend(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AEGIS_BOOTSTRAP_ADMIN_PASSWORD", BOOTSTRAP_PASSWORD)

    def _load(module_name: str):
        _clear_aegis_backend_modules()
        return importlib.import_module(module_name)

    return _load


@pytest.fixture
def hermes_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("AEGIS_JWT_SECRET", "test-jwt-secret-1234567890-abcdef")
    monkeypatch.setenv("AEGIS_BOOTSTRAP_ADMIN_PASSWORD", BOOTSTRAP_PASSWORD)
    return tmp_path


@pytest.fixture
def client(
    load_backend,
    monkeypatch: pytest.MonkeyPatch,
    hermes_home,
) -> Iterator[TestClient]:
    server = load_backend("aegis.backend.server")
    app = server.create_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_token(client: TestClient) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": BOOTSTRAP_PASSWORD},
    )
    assert response.status_code == 200
    payload = response.json()
    token = payload.get("access_token")
    assert isinstance(token, str) and token
    return token


@pytest.fixture
def auth_headers(auth_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth_token}"}
