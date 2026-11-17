"""``GET /api/kb/tree`` lists only what the KB can actually serve.

A single dangling symlink used to turn the whole listing into a 500, and symlinks leaving the
root were listed with their target's size and mtime.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from workagent.backend.routes.kb import build_kb_router


def _symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")


def test_tree_skips_dangling_and_escaping_symlinks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "wiki"
    (root / "notes").mkdir(parents=True)
    (root / "guide.md").write_text("# Guide\n", encoding="utf-8")
    outside_dir = tmp_path / "secrets"
    outside_dir.mkdir()
    (tmp_path / "outside.md").write_text("x" * 4096, encoding="utf-8")
    _symlink(root / "dangling.md", root / "deleted.md")
    _symlink(root / "escape.md", tmp_path / "outside.md")
    _symlink(root / "escape-dir", outside_dir)
    _symlink(root / "alias.md", root / "guide.md")

    monkeypatch.setenv("WORKAGENT_WIKI_PATH", str(root))
    app = FastAPI()
    app.include_router(build_kb_router())
    with TestClient(app) as client:
        response = client.get("/api/kb/tree")

    assert response.status_code == 200
    items = {item["name"]: item for item in response.json()["items"]}
    assert set(items) == {"notes", "alias.md", "guide.md"}
    assert items["notes"]["type"] == "dir"
    assert items["alias.md"] == {**items["guide.md"], "name": "alias.md", "path": "alias.md"}
