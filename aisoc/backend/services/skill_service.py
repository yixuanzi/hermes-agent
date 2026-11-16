"""Skills service adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hermes_cli.config import load_config

_MAX_APPENDIX_SIZE = 2 * 1024 * 1024  # 2 MB read limit for appendix files


class SkillNotFoundError(Exception):
    """Raised when a skill cannot be resolved by name."""


def _iter_skill_roots() -> list[Path]:
    from agent.skill_utils import get_external_skills_dirs
    from tools.skills_tool import SKILLS_DIR

    roots: list[Path] = []
    if SKILLS_DIR.exists():
        roots.append(SKILLS_DIR)
    roots.extend(get_external_skills_dirs())
    return roots


def _scan_skill_index() -> dict[str, dict[str, Any]]:
    from agent.skill_utils import iter_skill_index_files
    from tools.skills_tool import (
        MAX_DESCRIPTION_LENGTH,
        MAX_NAME_LENGTH,
        _EXCLUDED_SKILL_DIRS,
        _get_category_from_path,
        _parse_frontmatter,
        skill_matches_platform,
    )

    index: dict[str, dict[str, Any]] = {}
    for scan_dir in _iter_skill_roots():
        for skill_md in iter_skill_index_files(scan_dir, "SKILL.md"):
            if any(part in _EXCLUDED_SKILL_DIRS for part in skill_md.parts):
                continue
            try:
                content = skill_md.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError, OSError):
                continue
            try:
                frontmatter, body = _parse_frontmatter(content[:4000])
            except Exception:
                continue
            if not skill_matches_platform(frontmatter):
                continue
            skill_name = str(frontmatter.get("name", skill_md.parent.name))[:MAX_NAME_LENGTH]
            if not skill_name or skill_name in index:
                continue
            description = str(frontmatter.get("description", "")).strip()
            if not description:
                for line in body.strip().split("\n"):
                    line = line.strip()
                    if line and not line.startswith("#"):
                        description = line
                        break
            if len(description) > MAX_DESCRIPTION_LENGTH:
                description = description[: MAX_DESCRIPTION_LENGTH - 3] + "..."
            index[skill_name] = {
                "name": skill_name,
                "description": description,
                "category": _get_category_from_path(skill_md),
                "path": str(skill_md.parent),
                "skill_md_path": str(skill_md),
            }
    return index


def _resolve_skill_entry(skill_name: str) -> dict[str, Any]:
    index = _scan_skill_index()
    entry = index.get(skill_name)
    if entry is None:
        raise SkillNotFoundError(f"Skill '{skill_name}' not found.")
    return entry


def _list_appendix_files(skill_dir: Path) -> list[dict[str, str]]:
    appendix: list[dict[str, str]] = []
    for item in sorted(skill_dir.rglob("*"), key=lambda p: str(p)):
        if not item.is_file() or item.name == "SKILL.md":
            continue
        appendix.append(
            {
                "name": item.name,
                "path": str(item.relative_to(skill_dir)),
            }
        )
    return appendix


def list_skills() -> list[dict[str, Any]]:
    from hermes_cli.skills_config import get_disabled_skills

    config = load_config()
    disabled = get_disabled_skills(config)
    index = _scan_skill_index()
    skills = []
    for entry in sorted(index.values(), key=lambda s: ((s.get("category") or ""), s["name"])):
        skills.append(
            {
                "name": entry["name"],
                "description": entry.get("description", ""),
                "category": entry.get("category", ""),
                "path": entry["path"],
                "enabled": entry["name"] not in disabled,
            }
        )
    return skills


def get_skill_detail(skill_name: str) -> dict[str, Any]:
    entry = _resolve_skill_entry(skill_name)
    skill_dir = Path(entry["path"])
    skill_md = skill_dir / "SKILL.md"
    try:
        content = skill_md.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SkillNotFoundError(f"Skill '{skill_name}' main file not found.") from exc
    return {
        "name": entry["name"],
        "path": entry["path"],
        "content": content,
        "appendix": _list_appendix_files(skill_dir),
    }


def get_skill_appendix_content(skill_name: str, appendix_path: str) -> dict[str, Any]:
    from tools.path_security import has_traversal_component, validate_within_dir

    if not appendix_path:
        raise ValueError("appendix path is required")
    if has_traversal_component(appendix_path):
        raise ValueError("Path traversal ('..') is not allowed.")

    entry = _resolve_skill_entry(skill_name)
    skill_dir = Path(entry["path"])
    target_path = skill_dir / appendix_path
    traversal_error = validate_within_dir(target_path, skill_dir)
    if traversal_error:
        raise ValueError(traversal_error)
    if not target_path.exists() or not target_path.is_file():
        raise FileNotFoundError(f"Appendix file '{appendix_path}' not found.")
    if target_path.name == "SKILL.md":
        raise ValueError("SKILL.md is not an appendix file.")
    if target_path.stat().st_size > _MAX_APPENDIX_SIZE:
        raise ValueError(
            f"Appendix file too large ({target_path.stat().st_size} bytes, max {_MAX_APPENDIX_SIZE})."
        )
    try:
        content = target_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Appendix file is not UTF-8 text content.") from exc

    return {
        "name": target_path.name,
        "path": str(target_path.relative_to(skill_dir)),
        "content": content,
    }


def toggle_skill(name: str, enabled: bool) -> dict[str, Any]:
    from hermes_cli.skills_config import get_disabled_skills, save_disabled_skills

    config = load_config()
    disabled = get_disabled_skills(config)
    if enabled:
        disabled.discard(name)
    else:
        disabled.add(name)
    save_disabled_skills(config, disabled)
    return {"ok": True, "name": name, "enabled": enabled}


def reload_index() -> bool:
    # Current skill discovery is file-based and reads fresh on each call.
    # Returning True provides a stable API for future cached index support.
    return True
