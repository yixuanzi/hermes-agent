"""Drawer file previews, limited to files the requesting user's own agent turns wrote.

The drawer opens the files a turn's ``write_file``/``patch`` calls landed. A location is no
authorization: the process cwd is HERMES_HOME, which also holds ``.env``, ``config.yaml``, other
users' session dumps and every user's env partition, while the agent's file tools resolve
relative paths against the terminal cwd, which may be anywhere. A path is served only when this
user's agent wrote it (``DrawerFileLedger``) and the core read guard allows it.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable
import mimetypes
from pathlib import Path
import threading

from fastapi import HTTPException

from agent.file_safety import get_read_block_error
from hermes_constants import get_hermes_home
from utils import atomic_json_write, read_json_or_empty

from aegis.backend.models import DrawerFileResponse


LEDGER_FILENAME = "aegis_drawer_files.json"
# Oldest entries fall off first; a user's recent deliverables are what the drawer reopens.
MAX_FILES_PER_USER = 1000

_IMAGE_SUFFIXES = frozenset({".avif", ".bmp", ".gif", ".ico", ".jpeg", ".jpg", ".png", ".svg", ".webp"})
_DRAWER_FILE_TYPES = {
    ".css": "css",
    ".csv": "text",
    ".html": "html",
    ".htm": "html",
    ".java": "java",
    ".js": "javascript",
    ".json": "json",
    ".jsx": "javascript",
    ".md": "markdown",
    ".mdx": "markdown",
    ".markdown": "markdown",
    ".py": "python",
    ".rs": "rust",
    ".sh": "shell",
    ".sql": "sql",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".txt": "text",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
}

_NOT_FOUND = "Drawer file not found."


class DrawerFileLedger:
    """Per-user record of the files that user's agent turns wrote, persisted in HERMES_HOME."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._files: dict[str, list[str]] | None = None

    def _ledger_path(self) -> Path:
        return self._path or get_hermes_home() / LEDGER_FILENAME

    def _loaded(self) -> dict[str, list[str]]:
        if self._files is None:
            raw = read_json_or_empty(self._ledger_path()).get("users")
            self._files = {
                str(uid): [str(p) for p in paths if isinstance(p, str)]
                for uid, paths in (raw.items() if isinstance(raw, dict) else ())
                if isinstance(paths, list)
            }
        return self._files

    def record(self, user_id: str, paths: Iterable[str]) -> None:
        owner = str(user_id or "").strip()
        resolved = [str(Path(p).expanduser().resolve()) for p in paths if str(p).strip()]
        if not owner or not resolved:
            return
        with self._lock:
            files = self._loaded()
            mine = [p for p in files.get(owner, []) if p not in resolved] + resolved
            files[owner] = mine[-MAX_FILES_PER_USER:]
            atomic_json_write(self._ledger_path(), {"users": files}, mode=0o600)

    def owns(self, user_id: str, resolved_path: Path) -> bool:
        owner = str(user_id or "").strip()
        if not owner:
            return False
        with self._lock:
            return str(resolved_path) in self._loaded().get(owner, ())


def _workspace_root() -> Path:
    """Base for relative paths: the process cwd, which the runtime binds to HERMES_HOME."""
    return Path.cwd().resolve()


def _resolve_drawer_file(path: str, *, user_id: str, ledger: DrawerFileLedger) -> Path:
    requested = Path(str(path or "").strip())
    if not str(requested) or requested == Path("."):
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    candidate = requested if requested.is_absolute() else _workspace_root() / requested
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc
    # Unowned files answer exactly like missing ones, so the endpoint is no existence oracle.
    if not resolved.is_file() or not ledger.owns(user_id, resolved):
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    if get_read_block_error(str(resolved)):
        raise HTTPException(status_code=403, detail="Drawer path is a protected file.")
    return resolved


def _drawer_title(file_path: Path) -> str:
    try:
        return str(file_path.relative_to(_workspace_root()))
    except ValueError:
        return str(file_path)


def _drawer_file_type(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix in _IMAGE_SUFFIXES:
        return suffix[1:]
    return _DRAWER_FILE_TYPES.get(suffix, "text")


def _drawer_file_content(file_path: Path) -> str | None:
    if file_path.suffix.lower() in _IMAGE_SUFFIXES:
        media_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
        return f"data:{media_type};base64,{encoded}"
    try:
        return file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def drawer_file_response(path: str, *, user_id: str, ledger: DrawerFileLedger) -> DrawerFileResponse:
    file_path = _resolve_drawer_file(path, user_id=user_id, ledger=ledger)
    file_type = _drawer_file_type(file_path)
    content = _drawer_file_content(file_path)
    if content is None:
        file_type = "binary"
    return DrawerFileResponse(
        title=_drawer_title(file_path),
        type=file_type,
        content=content or "",
    )
