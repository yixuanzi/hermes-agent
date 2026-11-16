from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


_PNG = b"\x89PNG\r\n\x1a\nminimal"


def _upload(client: TestClient, headers: dict[str, str], name: str, contents: bytes, content_type: str) -> dict:
    response = client.post(
        "/api/chat/attachments",
        headers=headers,
        files={"file": (name, contents, content_type)},
    )
    assert response.status_code == 201, response.text
    return response.json()["attachment"]


def test_attachment_upload_caches_images_and_documents(
    client: TestClient,
    auth_headers: dict[str, str],
    hermes_home: Path,
) -> None:
    assert client.post("/api/chat/attachments", files={"file": ("x.txt", b"x", "text/plain")}).status_code == 401
    assert client.post(
        "/api/chat/attachments", headers=auth_headers, files={"file": ("empty.txt", b"", "text/plain")}
    ).status_code == 422
    assert client.post(
        "/api/chat/attachments", headers=auth_headers, files={"file": ("bad.png", b"not-a-png", "image/png")}
    ).status_code == 422

    image = _upload(client, auth_headers, "evidence.png", _PNG, "image/png")
    document = _upload(client, auth_headers, "ioc.json", b'{"sha256":"abc"}', "application/json")

    assert image["kind"] == "image"
    assert image["media_type"] == "image/png"
    assert Path(image["cache_path"]).is_file()
    assert Path(image["cache_path"]).is_relative_to(hermes_home / "cache" / "images")
    assert document["kind"] == "document"
    assert Path(document["cache_path"]).is_file()
    assert Path(document["cache_path"]).is_relative_to(hermes_home / "cache" / "documents")


def test_attachment_store_rejects_foreign_or_forged_references(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    attachment = _upload(client, auth_headers, "ioc.txt", b"ioc", "text/plain")
    store = client.app.state.chat_manager.attachments

    with pytest.raises(ValueError, match="unavailable"):
        store.resolve([{"id": attachment["id"]}], owner_id="another-user")
    with pytest.raises(ValueError, match="unavailable"):
        store.resolve([{"id": "att_forged", "cache_path": "/etc/passwd"}], owner_id="0000000000000001")


def test_document_turn_context_inlines_small_text(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    attachment = _upload(client, auth_headers, "ioc.json", b'{"ip":"203.0.113.7"}', "application/json")
    records = client.app.state.chat_manager.attachments.resolve(
        [{"id": attachment["id"]}], owner_id="0000000000000001"
    )

    from aegis.backend.chat.attachments import prepare_turn_message

    context = prepare_turn_message("Investigate this.", records, agent=object())
    assert isinstance(context, str)
    assert "[Content of ioc.json]" in context
    assert '"203.0.113.7"' in context


def test_image_turn_uses_native_content_parts_when_the_model_supports_vision(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attachment = _upload(client, auth_headers, "evidence.png", _PNG, "image/png")
    records = client.app.state.chat_manager.attachments.resolve(
        [{"id": attachment["id"]}], owner_id="0000000000000001"
    )
    monkeypatch.setattr("agent.image_routing.decide_image_input_mode", lambda *_args: "native")

    from aegis.backend.chat.attachments import prepare_turn_message

    class VisionAgent:
        provider = "test"
        model = "vision-test"

    message = prepare_turn_message("Inspect this image", records, agent=VisionAgent())
    assert isinstance(message, list)
    assert message[0]["type"] == "text"
    assert attachment["cache_path"] in message[0]["text"]
    assert message[1]["type"] == "image_url"
