from __future__ import annotations

import importlib

import httpx
from fastapi.testclient import TestClient


def test_app_entries_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/app-entries")

    assert response.status_code == 401


def test_app_entries_proxies_portal_and_reduces_entry_urls(
    load_backend,
    hermes_home,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AEGIS_PORTAL_URL", "https://portal.example.test/")
    monkeypatch.setenv("AEGIS_ORG_CODE", "XINGHAI-SH")
    monkeypatch.setenv("PUBLIC_SUBSCRIPTION_API_KEY", "server-only-key")
    server = load_backend("aegis.backend.server")
    service_module = importlib.import_module("aegis.backend.services.app_entry_service")
    captured: dict[str, object] = {}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, params, headers):
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "subscription_id": "sub-1",
                            "subscription_no": "SUB-001",
                            "subscription_status": "active",
                            "effective_to": "2027-08-01T00:00:00Z",
                            "product_service_code": "AEGIS-MDR",
                            "product_service_name": "Aegis Agentic MDR",
                            "sub_app_entry_url": "https://app.example.test",
                            "sub_entry_url": "https://fallback.example.test",
                        },
                        {
                            "subscription_id": "sub-2",
                            "subscription_no": "SUB-002",
                            "subscription_status": "expiring",
                            "effective_to": "2026-09-01T00:00:00Z",
                            "product_service_code": "AEGIS-XTIP",
                            "product_service_name": "Threat Intelligence",
                            "sub_app_entry_url": None,
                            "sub_entry_url": "https://service.example.test",
                        },
                        {
                            "subscription_id": "sub-3",
                            "subscription_no": "SUB-003",
                            "subscription_status": "active",
                            "effective_to": "2027-01-01T00:00:00Z",
                            "product_service_code": "AEGIS-PENTEST",
                            "product_service_name": "Penetration Testing",
                            "sub_app_entry_url": None,
                            "sub_entry_url": None,
                        },
                        {
                            "subscription_id": "sub-4",
                            "subscription_no": "SUB-004",
                            "subscription_status": "active",
                            "effective_to": "2027-01-01T00:00:00Z",
                            "product_service_code": "AEGIS-SAFE",
                            "product_service_name": "Safe Fallback Service",
                            "sub_app_entry_url": "javascript:alert(document.domain)",
                            "sub_entry_url": "https://safe.example.test",
                        },
                    ]
                },
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(service_module.httpx, "AsyncClient", FakeAsyncClient)
    app = server.create_app()

    with TestClient(app) as test_client:
        login = test_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "BootstrapPassword123!"},
        )
        token = login.json()["access_token"]
        response = test_client.get(
            "/api/app-entries",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert captured == {
        "timeout": 10.0,
        "url": "https://portal.example.test/api/v1/public/subscription-services",
        "params": {"organization_code": "XINGHAI-SH"},
        "headers": {"X-API-Key": "server-only-key"},
    }
    assert response.json() == {
        "organization_code": "XINGHAI-SH",
        "entries": [
            {
                "subscription_id": "sub-1",
                "subscription_no": "SUB-001",
                "subscription_status": "active",
                "effective_to": "2027-08-01T00:00:00Z",
                "product_service_code": "AEGIS-MDR",
                "product_service_name": "Aegis Agentic MDR",
                "entry_url": "https://app.example.test",
                "entry_source": "app",
            },
            {
                "subscription_id": "sub-2",
                "subscription_no": "SUB-002",
                "subscription_status": "expiring",
                "effective_to": "2026-09-01T00:00:00Z",
                "product_service_code": "AEGIS-XTIP",
                "product_service_name": "Threat Intelligence",
                "entry_url": "https://service.example.test",
                "entry_source": "subscription",
            },
            {
                "subscription_id": "sub-3",
                "subscription_no": "SUB-003",
                "subscription_status": "active",
                "effective_to": "2027-01-01T00:00:00Z",
                "product_service_code": "AEGIS-PENTEST",
                "product_service_name": "Penetration Testing",
                "entry_url": None,
                "entry_source": None,
            },
            {
                "subscription_id": "sub-4",
                "subscription_no": "SUB-004",
                "subscription_status": "active",
                "effective_to": "2027-01-01T00:00:00Z",
                "product_service_code": "AEGIS-SAFE",
                "product_service_name": "Safe Fallback Service",
                "entry_url": "https://safe.example.test",
                "entry_source": "subscription",
            },
        ],
    }
    assert "server-only-key" not in response.text


def test_app_entries_returns_service_unavailable_when_portal_config_is_missing(
    load_backend,
    hermes_home,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AEGIS_PORTAL_URL", raising=False)
    monkeypatch.delenv("PORTAL_BASE_URL", raising=False)
    monkeypatch.delenv("AEGIS_ORG_CODE", raising=False)
    monkeypatch.delenv("PUBLIC_SUBSCRIPTION_API_KEY", raising=False)
    server = load_backend("aegis.backend.server")
    app = server.create_app()

    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "BootstrapPassword123!"},
        )
        response = client.get(
            "/api/app-entries",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "App Entry organization is not configured."}
