from __future__ import annotations

import base64
from pathlib import Path

import pytest


_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


@pytest.fixture
def workspace(monkeypatch: pytest.MonkeyPatch, tmp_path_factory) -> Path:
    """Point the drawer API's workspace root (cwd) at an isolated directory."""
    workspace_dir = tmp_path_factory.mktemp("drawer-workspace")
    (workspace_dir / "reports").mkdir()
    (workspace_dir / "reports" / "incident.html").write_text(
        "<!doctype html><html><head><title>AISOC Incident Report</title></head><body></body></html>",
        encoding="utf-8",
    )
    (workspace_dir / "notes.md").write_text("# Notes\ncontent", encoding="utf-8")
    (workspace_dir / "script.py").write_text("print('hello aisoc')\n", encoding="utf-8")
    (workspace_dir / "evidence.png").write_bytes(_PNG)
    monkeypatch.chdir(workspace_dir)
    return workspace_dir


def test_chat_drawer_html_requires_authentication_and_returns_typed_document(
    client,
    auth_headers: dict[str, str],
    workspace: Path,
) -> None:
    path = "reports/incident.html"

    assert client.get("/api/chat/drawer-html", params={"path": path}).status_code == 401

    response = client.get(
        "/api/chat/drawer-html",
        params={"path": path},
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == path
    assert payload["type"] == "html"
    assert "<title>AISOC Incident Report</title>" in payload["content"]


def test_chat_drawer_html_returns_not_found_for_missing_document(
    client,
    auth_headers: dict[str, str],
    workspace: Path,
) -> None:
    response = client.get(
        "/api/chat/drawer-html",
        params={"path": "does-not-exist.html"},
        headers=auth_headers,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Drawer file not found."}


def test_chat_drawer_html_returns_text_and_images_from_the_workspace(
    client,
    auth_headers: dict[str, str],
    workspace: Path,
) -> None:
    text_response = client.get(
        "/api/chat/drawer-html",
        params={"path": "script.py"},
        headers=auth_headers,
    )
    assert text_response.status_code == 200
    assert text_response.json()["type"] == "python"
    assert "hello aisoc" in text_response.json()["content"]

    markdown_response = client.get(
        "/api/chat/drawer-html",
        params={"path": "notes.md"},
        headers=auth_headers,
    )
    assert markdown_response.status_code == 200
    assert markdown_response.json()["type"] == "markdown"
    assert markdown_response.json()["content"]

    image_response = client.get(
        "/api/chat/drawer-html",
        params={"path": "evidence.png"},
        headers=auth_headers,
    )
    assert image_response.status_code == 200
    assert image_response.json()["type"] == "png"
    assert image_response.json()["content"].startswith("data:image/png;base64,")


def test_chat_drawer_html_rejects_paths_outside_the_workspace(
    client,
    auth_headers: dict[str, str],
    workspace: Path,
) -> None:
    response = client.get(
        "/api/chat/drawer-html",
        params={"path": "/etc/hosts"},
        headers=auth_headers,
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "Drawer path is outside the workspace."}
