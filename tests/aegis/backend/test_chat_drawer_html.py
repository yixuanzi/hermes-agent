from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

from tests.aegis.backend.test_chat_ws import AUTH_TOKEN, _recv_until


_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
ADMIN_UID = "0000000000000001"


@pytest.fixture
def workspace(monkeypatch: pytest.MonkeyPatch, tmp_path_factory) -> Path:
    """Point the drawer API's workspace root (cwd) at an isolated directory."""
    workspace_dir = tmp_path_factory.mktemp("drawer-workspace")
    (workspace_dir / "reports").mkdir()
    (workspace_dir / "reports" / "incident.html").write_text(
        "<!doctype html><html><head><title>Aegis Incident Report</title></head><body></body></html>",
        encoding="utf-8",
    )
    (workspace_dir / "notes.md").write_text("# Notes\ncontent", encoding="utf-8")
    (workspace_dir / "script.py").write_text("print('hello aegis')\n", encoding="utf-8")
    (workspace_dir / "evidence.png").write_bytes(_PNG)
    (workspace_dir / "config.yaml").write_text("api_key: sk-secret\n", encoding="utf-8")
    (workspace_dir / ".env").write_text("GLM_API_KEY=sk-secret\n", encoding="utf-8")
    monkeypatch.chdir(workspace_dir)
    return workspace_dir


def _grant(client: TestClient, user_id: str, *paths: str) -> None:
    client.app.state.chat_manager.drawer_files.record(user_id, paths)


def _drawer(client: TestClient, headers: dict[str, str], path: str):
    return client.get("/api/chat/drawer-html", params={"path": path}, headers=headers)


def test_chat_drawer_html_requires_authentication_and_returns_typed_document(
    client,
    auth_headers: dict[str, str],
    workspace: Path,
) -> None:
    path = "reports/incident.html"
    _grant(client, ADMIN_UID, path)

    assert client.get("/api/chat/drawer-html", params={"path": path}).status_code == 401

    response = _drawer(client, auth_headers, path)
    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == path
    assert payload["type"] == "html"
    assert "<title>Aegis Incident Report</title>" in payload["content"]


def test_chat_drawer_html_returns_not_found_for_missing_document(
    client,
    auth_headers: dict[str, str],
    workspace: Path,
) -> None:
    response = _drawer(client, auth_headers, "does-not-exist.html")

    assert response.status_code == 404
    assert response.json() == {"detail": "Drawer file not found."}


def test_chat_drawer_html_returns_text_and_images_the_user_wrote(
    client,
    auth_headers: dict[str, str],
    workspace: Path,
) -> None:
    _grant(client, ADMIN_UID, "script.py", "notes.md", str(workspace / "evidence.png"))

    text_response = _drawer(client, auth_headers, "script.py")
    assert text_response.status_code == 200
    assert text_response.json()["type"] == "python"
    assert "hello aegis" in text_response.json()["content"]

    markdown_response = _drawer(client, auth_headers, "notes.md")
    assert markdown_response.status_code == 200
    assert markdown_response.json()["type"] == "markdown"
    assert markdown_response.json()["content"]

    image_response = _drawer(client, auth_headers, "evidence.png")
    assert image_response.status_code == 200
    assert image_response.json()["type"] == "png"
    assert image_response.json()["content"].startswith("data:image/png;base64,")


def test_chat_drawer_html_hides_workspace_files_the_user_did_not_write(
    client,
    auth_headers: dict[str, str],
    workspace: Path,
) -> None:
    """HERMES_HOME is the cwd: its config and other users' files are not previewable."""
    _grant(client, "someone-else", "notes.md")

    for path in ("config.yaml", "notes.md", "script.py"):
        response = _drawer(client, auth_headers, path)
        assert response.status_code == 404, path
        assert response.json() == {"detail": "Drawer file not found."}


def test_chat_drawer_html_refuses_protected_files_even_when_written(
    client,
    auth_headers: dict[str, str],
    workspace: Path,
) -> None:
    _grant(client, ADMIN_UID, ".env")

    response = _drawer(client, auth_headers, ".env")

    assert response.status_code == 403
    assert "sk-secret" not in response.text


def test_chat_drawer_html_follows_ownership_not_location(
    client,
    auth_headers: dict[str, str],
    workspace: Path,
    tmp_path_factory,
) -> None:
    """The file tools resolve relative paths against the terminal cwd, which may sit outside
    HERMES_HOME: a deliverable the user's agent wrote there is previewable, nothing else is."""
    outside = tmp_path_factory.mktemp("terminal-cwd") / "report.html"
    outside.write_text("<h1>outside</h1>", encoding="utf-8")
    _grant(client, ADMIN_UID, str(outside))

    written = _drawer(client, auth_headers, str(outside))
    not_written = _drawer(client, auth_headers, "/etc/hosts")

    assert written.status_code == 200
    assert written.json()["title"] == str(outside.resolve())
    assert not_written.status_code == 404


class _WritesReportAgent:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.stream_delta_callback = None
        self.tool_start_callback = None
        self.tool_complete_callback = None

    def run_conversation(self, user_message: str, **_kwargs: Any) -> dict[str, Any]:
        report = Path.cwd() / "reports" / "incident.html"
        self.tool_complete_callback(
            "write-report",
            "write_file",
            {"path": str(report)},
            json.dumps({"resolved_path": str(report)}),
        )
        return {"final_response": "done", "completed": True}


def test_chat_drawer_html_serves_a_file_after_the_users_turn_wrote_it(
    load_backend,
    hermes_home,
    workspace: Path,
) -> None:
    server = load_backend("aegis.backend.server")
    app = server.create_app()
    app.state.chat_manager.set_agent_factory(lambda session_id: _WritesReportAgent(session_id))
    headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}

    with TestClient(app) as client:
        assert _drawer(client, headers, "reports/incident.html").status_code == 404
        with client.websocket_connect(f"/api/chat/ws?token={AUTH_TOKEN}") as ws:
            ws.send_json({"type": "session.bind", "title": "Report"})
            session_id = _recv_until(ws, "session.bound")["session_id"]
            ws.send_json({"type": "message.send", "session_id": session_id, "text": "write it"})
            _recv_until(ws, "message.completed")
        response = _drawer(client, headers, "reports/incident.html")

    assert response.status_code == 200
    assert response.json()["type"] == "html"
    # The grant survives a restart: a fresh ledger reads it back from HERMES_HOME.
    from aegis.backend.chat.drawer import DrawerFileLedger

    assert DrawerFileLedger().owns(ADMIN_UID, (workspace / "reports" / "incident.html").resolve())
