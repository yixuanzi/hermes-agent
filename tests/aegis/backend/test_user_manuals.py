from __future__ import annotations

from fastapi.testclient import TestClient


def _set_manual_directory(client: TestClient, tmp_path) -> None:
    docs_dir = tmp_path / "manuals"
    docs_dir.mkdir()
    (docs_dir / "z-guide.md").write_text("# Zulu Guide\n\nA manual.", encoding="utf-8")
    (docs_dir / "a-guide.md").write_text("# Alpha Guide\n\n| A | B |\n| - | - |\n| 1 | 2 |", encoding="utf-8")
    (docs_dir / "hidden.txt").write_text("not a manual", encoding="utf-8")
    nested = docs_dir / "nested"
    nested.mkdir()
    (nested / "nested.md").write_text("# Nested", encoding="utf-8")
    client.app.state.user_manual_service.docs_dir = docs_dir


def test_user_manuals_require_auth_and_list_top_level_markdown(
    client: TestClient,
    auth_headers: dict[str, str],
    tmp_path,
) -> None:
    _set_manual_directory(client, tmp_path)

    assert client.get("/api/user-manuals").status_code == 401

    response = client.get("/api/user-manuals", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {
        "manuals": [
            {"id": "a-guide", "title": "Alpha Guide"},
            {"id": "z-guide", "title": "Zulu Guide"},
        ],
        "default_manual_id": "a-guide",
    }


def test_user_manual_content_uses_only_server_listed_ids(
    client: TestClient,
    auth_headers: dict[str, str],
    tmp_path,
) -> None:
    _set_manual_directory(client, tmp_path)

    content = client.get("/api/user-manuals/a-guide", headers=auth_headers)
    assert content.status_code == 200
    assert content.json() == {
        "id": "a-guide",
        "title": "Alpha Guide",
        "content": "# Alpha Guide\n\n| A | B |\n| - | - |\n| 1 | 2 |",
    }

    assert client.get("/api/user-manuals/missing", headers=auth_headers).status_code == 404
    assert client.get("/api/user-manuals/..%2Fhidden", headers=auth_headers).status_code == 404

    (tmp_path / "manuals" / "a-guide.md").unlink()
    assert client.get("/api/user-manuals/a-guide", headers=auth_headers).status_code == 404
