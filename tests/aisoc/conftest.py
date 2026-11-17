from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import aisoc.backend.services.user_store as _user_store_module
from aisoc.backend.config import load_aisoc_settings
from aisoc.backend.server import create_app


BOOTSTRAP_PASSWORD = "test-admin-password-123"


@pytest.fixture
def test_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AISOC_BOOTSTRAP_ADMIN_PASSWORD", BOOTSTRAP_PASSWORD)
    monkeypatch.setenv("AISOC_JWT_SECRET", "test-jwt-secret-1234567890-abcdef")
    # The user store is a process-wide singleton; reset it so each test's
    # fresh HERMES_HOME tempdir gets its own store instead of inheriting
    # mutated state (e.g. a changed admin password) from an earlier test.
    monkeypatch.setattr(_user_store_module, "_STORE", None)
    settings = load_aisoc_settings()
    app = create_app(settings)
    return TestClient(app)


@pytest.fixture
def auth_token(test_client: TestClient) -> str:
    response = test_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": BOOTSTRAP_PASSWORD},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    assert isinstance(token, str) and token
    return token


@pytest.fixture
def auth_headers(auth_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth_token}"}

