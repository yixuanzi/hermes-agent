"""Memory service adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException
from hermes_constants import get_hermes_home


def _memories_dir() -> Path:
    return get_hermes_home() / "memories"


def _soul_path() -> Path:
    return get_hermes_home() / "SOUL.md"


def _user_path() -> Path:
    return _memories_dir() / "USER.md"


def list_memory_bundle() -> dict[str, Any]:
    memories_dir = _memories_dir()
    files = []
    if memories_dir.exists():
        for entry in sorted(memories_dir.glob("*.md")):
            files.append(
                {
                    "name": entry.name,
                    "path": str(entry),
                    "exists": entry.exists(),
                }
            )
    return {
        "soul": {"name": "SOUL.md", "path": str(_soul_path()), "exists": _soul_path().exists()},
        "user_preferences": {
            "name": "USER.md",
            "path": str(_user_path()),
            "exists": _user_path().exists(),
        },
        "memory_files": files,
    }


def read_file(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read {path.name}: {exc}")


def write_file(path: Path, content: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write {path.name}: {exc}")


def read_soul() -> dict[str, Any]:
    path = _soul_path()
    return {"name": path.name, "path": str(path), "content": read_file(path)}


def write_soul(content: str) -> dict[str, Any]:
    path = _soul_path()
    write_file(path, content)
    return {"ok": True, "path": str(path)}


def read_user_preferences() -> dict[str, Any]:
    path = _user_path()
    return {"name": path.name, "path": str(path), "content": read_file(path)}


def write_user_preferences(content: str) -> dict[str, Any]:
    path = _user_path()
    write_file(path, content)
    return {"ok": True, "path": str(path)}


def _resolve_memory_file(name: str) -> Path:
    candidate = (name or "").strip()
    if not candidate:
        raise HTTPException(status_code=400, detail="Memory file name is required")
    if "/" in candidate or "\\" in candidate:
        raise HTTPException(status_code=400, detail="Path segments are not allowed")
    if ".." in candidate.split("/") or ".." in candidate.split("\\"):
        raise HTTPException(status_code=400, detail="Path traversal is not allowed")
    if not candidate.endswith(".md"):
        raise HTTPException(status_code=400, detail="Only .md memory files are supported")
    resolved = (_memories_dir() / candidate).resolve()
    allowed_root = _memories_dir().resolve()
    if not str(resolved).startswith(str(allowed_root)):
        raise HTTPException(status_code=400, detail="Access denied: path escapes memory directory")
    return resolved


def read_memory_file(name: str) -> dict[str, Any]:
    path = _resolve_memory_file(name)
    return {"name": path.name, "path": str(path), "content": read_file(path)}


def write_memory_file(name: str, content: str) -> dict[str, Any]:
    path = _resolve_memory_file(name)
    write_file(path, content)
    return {"ok": True, "path": str(path)}

