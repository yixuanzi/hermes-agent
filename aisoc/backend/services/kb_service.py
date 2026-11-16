"""Knowledge base service — file tree listing and document reading."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException

_MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MB read limit for documents


def _wiki_root() -> Path:
    """Return the knowledge base root from ``AISOC_WIKI_PATH``."""
    raw = (os.environ.get("AISOC_WIKI_PATH") or "").strip()
    if not raw:
        raise HTTPException(
            status_code=503,
            detail="Knowledge base is not configured (AISOC_WIKI_PATH not set).",
        )
    root = Path(raw).resolve()
    if not root.is_dir():
        raise HTTPException(
            status_code=503,
            detail=f"Knowledge base path does not exist or is not a directory: {root}",
        )
    return root


def _safe_resolve(raw_path: str, root: Path) -> Path:
    """Resolve *raw_path* within *root*, rejecting traversal and symlink escapes."""
    from tools.path_security import has_traversal_component, validate_within_dir

    if has_traversal_component(raw_path):
        raise HTTPException(status_code=400, detail="Path traversal ('..') is not allowed.")
    target = (root / raw_path).resolve() if raw_path else root
    error = validate_within_dir(target, root)
    if error:
        raise HTTPException(status_code=403, detail=error)
    return target


def list_tree(cwd: str = "") -> dict[str, Any]:
    """List immediate children under *cwd* relative to the wiki root."""
    root = _wiki_root()
    target = _safe_resolve(cwd, root)
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {cwd or '/'}")

    dirs: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []

    try:
        entries = sorted(target.iterdir(), key=lambda e: e.name.lower())
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=f"Permission denied: {exc}") from exc

    for entry in entries:
        rel = str(entry.relative_to(root))
        stat = entry.stat()
        node = {
            "name": entry.name,
            "path": rel,
            "type": "dir" if entry.is_dir() else "file",
            "size": stat.st_size if entry.is_file() else 0,
            "modified": int(stat.st_mtime),
        }
        (dirs if entry.is_dir() else files).append(node)

    return {"root": str(root), "cwd": cwd or "/", "items": dirs + files}


def read_document(path: str) -> dict[str, Any]:
    """Return the text content of a single file inside the wiki."""
    if not path:
        raise HTTPException(status_code=400, detail="File path is required.")

    root = _wiki_root()
    target = _safe_resolve(path, root)

    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if target.is_dir():
        raise HTTPException(status_code=400, detail=f"Path is a directory, not a file: {path}")
    if target.stat().st_size > _MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({target.stat().st_size} bytes, max {_MAX_FILE_SIZE}).",
        )

    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=415, detail="File is not UTF-8 text content."
        ) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=f"Permission denied: {exc}") from exc

    return {
        "name": target.name,
        "path": str(target.relative_to(root)),
        "size": target.stat().st_size,
        "modified": int(target.stat().st_mtime),
        "content": content,
    }
