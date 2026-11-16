"""Public, read-only JavaScript assets bundled with Aegis skills."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


_SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"
_NOT_FOUND = HTTPException(status_code=404, detail="Skill asset not found.")
_RESPONSE_HEADERS = {
    "Cache-Control": "public, max-age=3600",
    "X-Content-Type-Options": "nosniff",
}


def _resolve_javascript_asset(skills_root: Path, asset_path: str) -> Path:
    """Return a regular JavaScript file strictly contained by ``skills_root``."""
    root = skills_root.resolve()
    requested = root / asset_path

    try:
        relative_path = requested.relative_to(root)
    except ValueError:
        raise _NOT_FOUND from None

    current = root
    for component in relative_path.parts:
        if component in {".", ".."}:
            raise _NOT_FOUND
        current /= component
        if current.is_symlink():
            raise _NOT_FOUND

    try:
        resolved = requested.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        raise _NOT_FOUND from None

    if not resolved.is_file() or resolved.suffix != ".js":
        raise _NOT_FOUND
    return resolved


def build_skills_static_router(skills_root: Path | None = None) -> APIRouter:
    """Build the public router for regular JavaScript assets under Aegis skills."""
    root = skills_root or _SKILLS_ROOT
    router = APIRouter(prefix="/static/skills", tags=["skill-assets"])

    @router.get("/{asset_path:path}", include_in_schema=False)
    async def get_skill_javascript(asset_path: str) -> FileResponse:
        return FileResponse(
            _resolve_javascript_asset(root, asset_path),
            media_type="application/javascript",
            headers=_RESPONSE_HEADERS,
        )

    return router
