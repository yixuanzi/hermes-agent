"""Tests for KB service (kb_service)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from aisoc.backend.services.kb_service import list_tree, read_document


@pytest.fixture()
def wiki_dir(tmp_path: Path):
    """Create a temporary wiki directory with sample files."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "intro.md").write_text("# Intro\nHello world", encoding="utf-8")
    (tmp_path / "readme.md").write_text("# README", encoding="utf-8")
    # binary file
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return tmp_path


def _set_wiki_env(path: Path):
    """Patch AISOC_WIKI_PATH to the given directory."""
    return patch.dict(os.environ, {"AISOC_WIKI_PATH": str(path)})


# ── list_tree ────────────────────────────────────────────────────────

class TestListTree:
    def test_root_listing(self, wiki_dir):
        with _set_wiki_env(wiki_dir):
            result = list_tree("")
        names = [item["name"] for item in result["items"]]
        assert "docs" in names
        assert "readme.md" in names
        assert result["cwd"] == "/"

    def test_subdirectory(self, wiki_dir):
        with _set_wiki_env(wiki_dir):
            result = list_tree("docs")
        names = [item["name"] for item in result["items"]]
        assert names == ["intro.md"]

    def test_dirs_before_files(self, wiki_dir):
        with _set_wiki_env(wiki_dir):
            result = list_tree("")
        types = [item["type"] for item in result["items"]]
        # All dirs should appear before any file
        first_file = next((i for i, t in enumerate(types) if t == "file"), len(types))
        assert all(t == "dir" for t in types[:first_file])

    def test_file_metadata(self, wiki_dir):
        with _set_wiki_env(wiki_dir):
            result = list_tree("")
        readme = next(i for i in result["items"] if i["name"] == "readme.md")
        assert readme["type"] == "file"
        assert readme["size"] > 0
        assert readme["modified"] > 0
        assert readme["path"] == "readme.md"

    def test_traversal_rejected(self, wiki_dir):
        with _set_wiki_env(wiki_dir):
            with pytest.raises(HTTPException) as exc_info:
                list_tree("../../etc")
            assert exc_info.value.status_code == 400

    def test_not_a_directory(self, wiki_dir):
        with _set_wiki_env(wiki_dir):
            with pytest.raises(HTTPException) as exc_info:
                list_tree("readme.md")
            assert exc_info.value.status_code == 400

    def test_missing_env_var(self):
        with patch.dict(os.environ, {}, clear=True):
            # Ensure AISOC_WIKI_PATH is not set
            os.environ.pop("AISOC_WIKI_PATH", None)
            with pytest.raises(HTTPException) as exc_info:
                list_tree("")
            assert exc_info.value.status_code == 503


# ── read_document ────────────────────────────────────────────────────

class TestReadDocument:
    def test_read_text_file(self, wiki_dir):
        with _set_wiki_env(wiki_dir):
            result = read_document("readme.md")
        assert result["name"] == "readme.md"
        assert result["content"] == "# README"

    def test_read_nested_file(self, wiki_dir):
        with _set_wiki_env(wiki_dir):
            result = read_document("docs/intro.md")
        assert "Hello world" in result["content"]

    def test_file_not_found(self, wiki_dir):
        with _set_wiki_env(wiki_dir):
            with pytest.raises(HTTPException) as exc_info:
                read_document("nope.md")
            assert exc_info.value.status_code == 404

    def test_directory_rejected(self, wiki_dir):
        with _set_wiki_env(wiki_dir):
            with pytest.raises(HTTPException) as exc_info:
                read_document("docs")
            assert exc_info.value.status_code == 400

    def test_empty_path_rejected(self, wiki_dir):
        with _set_wiki_env(wiki_dir):
            with pytest.raises(HTTPException) as exc_info:
                read_document("")
            assert exc_info.value.status_code == 400

    def test_binary_file_rejected(self, wiki_dir):
        with _set_wiki_env(wiki_dir):
            with pytest.raises(HTTPException) as exc_info:
                read_document("image.png")
            assert exc_info.value.status_code == 415

    def test_traversal_rejected(self, wiki_dir):
        with _set_wiki_env(wiki_dir):
            with pytest.raises(HTTPException) as exc_info:
                read_document("../../etc/passwd")
            assert exc_info.value.status_code == 400
