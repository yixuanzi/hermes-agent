from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from workagent.backend.routes.kb import build_kb_router
from workagent.backend.services import kb_service


def _client(monkeypatch: pytest.MonkeyPatch, root: Path) -> TestClient:
    monkeypatch.setenv("WORKAGENT_WIKI_PATH", str(root))
    app = FastAPI()
    app.include_router(build_kb_router())
    return TestClient(app)


def test_write_document_updates_existing_file_and_returns_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "wiki"
    document = root / "02-Wiki" / "guide.md"
    document.parent.mkdir(parents=True)
    document.write_text("# Before\n", encoding="utf-8")

    with _client(monkeypatch, root) as client:
        response = client.put(
            "/api/kb/documents",
            json={"path": "02-Wiki/guide.md", "content": "# After\n\nUpdated."},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["path"] == "02-Wiki/guide.md"
    assert payload["size"] == len("# After\n\nUpdated.".encode("utf-8"))
    assert document.read_text(encoding="utf-8") == "# After\n\nUpdated."


@pytest.mark.parametrize(
    ("path", "status_code"),
    [
        ("missing.md", 404),
        ("../outside.md", 400),
        ("", 400),
    ],
)
def test_write_document_rejects_invalid_targets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, path: str, status_code: int
) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    (tmp_path / "outside.md").write_text("outside", encoding="utf-8")

    with _client(monkeypatch, root) as client:
        response = client.put("/api/kb/documents", json={"path": path, "content": "new"})

    assert response.status_code == status_code


def test_write_document_rejects_directory_and_external_symlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "folder").mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    link = root / "linked.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")

    with _client(monkeypatch, root) as client:
        directory_response = client.put(
            "/api/kb/documents", json={"path": "folder", "content": "new"}
        )
        link_response = client.put(
            "/api/kb/documents", json={"path": "linked.md", "content": "new"}
        )

    assert directory_response.status_code == 400
    assert link_response.status_code == 403
    assert outside.read_text(encoding="utf-8") == "outside"


def test_write_document_maps_filesystem_failure_to_stable_500(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "wiki"
    document = root / "guide.md"
    root.mkdir()
    document.write_text("before", encoding="utf-8")

    def fail_write(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_bytes", fail_write)
    with _client(monkeypatch, root) as client:
        response = client.put(
            "/api/kb/documents", json={"path": "guide.md", "content": "after"}
        )

    assert response.status_code == 500
    assert response.json() == {"detail": "Failed to write knowledge base document."}


def test_write_document_rejects_oversized_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "guide.md").write_text("before", encoding="utf-8")

    with _client(monkeypatch, root) as client:
        response = client.put(
            "/api/kb/documents",
            json={"path": "guide.md", "content": "x" * (kb_service._MAX_FILE_SIZE + 1)},
        )

    assert response.status_code == 413
