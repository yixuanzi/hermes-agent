"""Sync AISOC-bundled skills into the active Hermes profile at startup.

The agent runtime discovers skills in ``$HERMES_HOME/skills/`` only, while the
canonical sources live in the repository under ``aisoc/skills/``. This
installer copies each bundled skill over on server start.

Safety: a managed skill directory is stamped with a ``.aisoc-managed`` marker
file. Only directories carrying the marker (or not existing yet) are ever
replaced — an operator's hand-made or hand-modified skill directory without
the marker is left untouched.
"""

from __future__ import annotations

import filecmp
import os
from pathlib import Path
import shutil


MANAGED_MARKER = ".aisoc-managed"

_BUNDLED_SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"


def bundled_skills_root() -> Path:
    return _BUNDLED_SKILLS_ROOT


def _hermes_home() -> Path:
    env_home = str(os.environ.get("HERMES_HOME") or "").strip()
    if env_home:
        return Path(env_home)
    try:
        from hermes_constants import get_hermes_home

        return Path(str(get_hermes_home()))
    except Exception:
        return Path.cwd()


def _directories_differ(source: Path, target: Path) -> bool:
    comparison = filecmp.dircmp(source, target, ignore=[MANAGED_MARKER, "__pycache__"])
    if comparison.left_only or comparison.right_only or comparison.diff_files or comparison.funny_files:
        return True
    return any(
        _directories_differ(source / name, target / name)
        for name in comparison.common_dirs
    )


def install_bundled_skills(
    *,
    source_root: Path | None = None,
    skills_root: Path | None = None,
) -> list[str]:
    """Install/refresh bundled skills; returns the names that were (re)installed."""
    resolved_source_root = source_root or bundled_skills_root()
    resolved_skills_root = skills_root or (_hermes_home() / "skills")
    if not resolved_source_root.is_dir():
        return []

    installed: list[str] = []
    for source in sorted(resolved_source_root.iterdir()):
        if not source.is_dir() or not (source / "SKILL.md").is_file():
            continue
        target = resolved_skills_root / source.name
        if target.exists():
            if not (target / MANAGED_MARKER).is_file():
                # Operator-owned directory: never overwrite.
                continue
            if not _directories_differ(source, target):
                continue
            shutil.rmtree(target)
        resolved_skills_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
        (target / MANAGED_MARKER).write_text(
            "Managed by aisoc.backend.services.skill_installer — local edits are overwritten on AISOC start.\n",
            encoding="utf-8",
        )
        installed.append(source.name)
    return installed
