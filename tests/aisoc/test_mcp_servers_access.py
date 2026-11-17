"""MCP servers are host-wide config: edits keep what the form does not show,
only admins write, and other users never see credential-bearing parts."""

from __future__ import annotations

import pytest

from tests.aisoc.conftest import BOOTSTRAP_PASSWORD

DIFY_URL = "https://api.dify.ai/mcp/server/k3y-s3cr3t-path/mcp"


@pytest.fixture
def member_headers(test_client, auth_headers) -> dict[str, str]:
    created = test_client.post(
        "/api/users",
        headers=auth_headers,
        json={
            "username": "analyst",
            "password": "analyst-password-1",
            "email": "analyst@example.com",
            "status": "enabled",
        },
    )
    assert created.status_code == 201
    token = test_client.post(
        "/api/auth/login", json={"username": "analyst", "password": "analyst-password-1"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _create(test_client, headers, **body):
    resp = test_client.post("/api/mcp-servers", headers=headers, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_edit_keeps_tool_filter_and_disabled_state(test_client, auth_headers):
    _create(test_client, auth_headers, name="intel", url="https://intel.example.com/mcp")
    test_client.put("/api/mcp-servers/intel/tools", headers=auth_headers, json={"tools": ["lookup"]})
    test_client.put("/api/mcp-servers/intel/enabled", headers=auth_headers, json={"enabled": False})

    resp = test_client.put(
        "/api/mcp-servers/intel",
        headers=auth_headers,
        json={"url": "https://intel2.example.com/mcp", "auth": "none"},
    )

    assert resp.status_code == 200, resp.text
    server = resp.json()
    assert server["url"] == "https://intel2.example.com/mcp"
    assert server["tools"] == ["lookup"]
    assert server["enabled"] is False


def test_only_admins_write_probe_or_reload(test_client, auth_headers, member_headers):
    _create(test_client, auth_headers, name="intel", url="https://intel.example.com/mcp")
    writes = [
        ("post", "/api/mcp-servers", {"name": "x", "command": "uvx", "args": ["evil"]}),
        ("put", "/api/mcp-servers/intel", {"command": "sh", "args": ["-c", "id"]}),
        ("delete", "/api/mcp-servers/intel", None),
        ("put", "/api/mcp-servers/intel/enabled", {"enabled": False}),
        ("put", "/api/mcp-servers/intel/tools", {"tools": ["a"]}),
        ("post", "/api/mcp-servers/intel/test", None),
        ("post", "/api/mcp-servers/reload", None),
    ]

    statuses = {
        (method, url): test_client.request(method, url, headers=member_headers, json=body).status_code
        for method, url, body in writes
    }

    assert set(statuses.values()) == {403}, statuses
    assert test_client.get("/api/mcp-servers/intel", headers=auth_headers).json()["enabled"] is True


def test_members_read_servers_without_credentials(test_client, auth_headers, member_headers):
    _create(test_client, auth_headers, name="dify", url=DIFY_URL)
    _create(
        test_client,
        auth_headers,
        name="local",
        command="uvx",
        args=["intel-mcp", "--token", "tok-s3cr3t"],
        env={"INTEL_API_KEY": "sk-s3cr3t-value"},
    )

    member_view = test_client.get("/api/mcp-servers", headers=member_headers)
    admin_view = test_client.get("/api/mcp-servers/dify", headers=auth_headers).json()

    assert member_view.status_code == 200
    assert "s3cr3t" not in member_view.text
    servers = {server["name"]: server for server in member_view.json()}
    assert servers["dify"]["url"] == "https://api.dify.ai/***"
    assert servers["local"]["command"] == "uvx"
    assert list(servers["local"]["env"]) == ["INTEL_API_KEY"]
    assert "s3cr3t" not in test_client.get("/api/mcp-servers/dify", headers=member_headers).text
    assert admin_view["url"] == DIFY_URL
