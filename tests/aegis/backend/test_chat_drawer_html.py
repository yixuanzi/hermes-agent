from __future__ import annotations


def test_chat_drawer_html_requires_authentication_and_returns_typed_document(
    client,
    auth_headers: dict[str, str],
) -> None:
    path = "aegis/docs/2026-06-07-aegis-architecture-showcase.html"

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
    assert "<title>Aegis Architecture Showcase</title>" in payload["content"]


def test_chat_drawer_html_returns_not_found_for_missing_document(
    client,
    auth_headers: dict[str, str],
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
) -> None:
    text_response = client.get(
        "/api/chat/drawer-html",
        params={"path": "aegis/frontend/src/types.ts"},
        headers=auth_headers,
    )
    assert text_response.status_code == 200
    assert text_response.json()["type"] == "typescript"
    assert "export interface Message" in text_response.json()["content"]

    markdown_response = client.get(
        "/api/chat/drawer-html",
        params={"path": "README.md"},
        headers=auth_headers,
    )
    assert markdown_response.status_code == 200
    assert markdown_response.json()["type"] == "markdown"
    assert markdown_response.json()["content"]

    image_response = client.get(
        "/api/chat/drawer-html",
        params={"path": "aegis/docs/assets/aegis-three-layer-starmapping-2k-reference-v3.png"},
        headers=auth_headers,
    )
    assert image_response.status_code == 200
    assert image_response.json()["type"] == "png"
    assert image_response.json()["content"].startswith("data:image/png;base64,")


def test_chat_drawer_html_rejects_paths_outside_the_workspace(
    client,
    auth_headers: dict[str, str],
) -> None:
    response = client.get(
        "/api/chat/drawer-html",
        params={"path": "/etc/hosts"},
        headers=auth_headers,
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "Drawer path is outside the workspace."}
