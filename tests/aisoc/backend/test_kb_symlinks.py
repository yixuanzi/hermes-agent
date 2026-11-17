"""KB tree listing applies the document reader's symlink boundary per entry."""

from __future__ import annotations

from pathlib import Path

import pytest

from aisoc.backend.services.kb_service import list_tree


@pytest.fixture
def wiki(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "notes.md").write_text("# notes", encoding="utf-8")
    monkeypatch.setenv("AISOC_WIKI_PATH", str(root))
    return root


@pytest.mark.platforms("posix")
def test_dangling_symlink_is_skipped_not_a_500(wiki: Path):
    (wiki / "gone.md").symlink_to(wiki / "never-existed.md")

    names = [item["name"] for item in list_tree("")["items"]]

    assert names == ["notes.md"]


@pytest.mark.platforms("posix")
def test_symlink_escaping_the_root_is_not_listed(wiki: Path, tmp_path: Path):
    outside = tmp_path / "secret.txt"
    outside.write_text("x" * 4096, encoding="utf-8")
    (wiki / "leak.txt").symlink_to(outside)
    (wiki / "alias.md").symlink_to(wiki / "notes.md")

    items = {item["name"]: item for item in list_tree("")["items"]}

    assert set(items) == {"alias.md", "notes.md"}
    assert items["alias.md"]["size"] == len("# notes")
