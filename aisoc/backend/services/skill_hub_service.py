"""Tenant SkillHub integration service for AISOC's Integrator > SkillHub page.

The tenant SkillHub (``skill_hub.url`` / ``skill_hub.api_key`` in
config.yaml) is a third-party enterprise skill catalog — unrelated to
Hermes's own built-in skill-hub sources in ``tools/skills_hub.py``
(github/skills-sh/clawhub/...). It ships a single-file bash CLI that
self-installs via its own agent-facing bootstrap guide at
``GET {url}/api/v1/script/install``; this module follows that guide
programmatically instead of shelling an agent session out to read it:

    GET {url}/api/v1/script/bash              -> the CLI script itself
    skillhub install <name>                    -> GET {url}/api/v1/skills/<name>/?install=true
                                                   writes plaintext SKILL.md to
                                                   ~/.agents/skills/<name>/SKILL.md
    skillhub list [search]                      -> GET {url}/api/v1/skills/?search=...

All vendor endpoints take ``Authorization: Bearer $SKILLHUB_API_KEY``.

There is no vendor uninstall/update endpoint: "sync" re-runs install (the
vendor CLI overwrites SKILL.md in place); "uninstall" is a plain rmtree of
the install directory — this is exactly the meeting's decision that AISOC
only manages local state, never skill content ("卸载的话其实就是删除嘛").

``~/.agents/skills`` must be listed in ``skills.external_dirs`` for the
Hermes agent runtime to actually discover skills installed here (it is
even the canonical example value in ``hermes_cli/config_defaults.py``).
``_ensure_external_dir_registered`` makes this self-healing after a
successful install so a fresh tenant deployment doesn't silently install
skills nothing can see, while a failed install leaves config.yaml alone.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from typing import Any
from datetime import UTC

logger = logging.getLogger(__name__)

_EXTERNAL_DIR_CONFIG_VALUE = "~/.agents/skills"
_REQUEST_TIMEOUT_SECONDS = 60
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def _skillhub_bin() -> Path:
    # NOT a module-level constant: aisoc.backend.agent_runtime.prepare_hermes_home()
    # repoints $HOME at the active Hermes profile's home dir (e.g.
    # ~/.hermes/profiles/<name>/home) *after* this module is first imported —
    # freezing Path.home() here would resolve against the wrong $HOME and
    # silently diverge from what the skillhub subprocess (which inherits the
    # then-current os.environ) actually writes to.
    return Path.home() / ".local" / "bin" / "skillhub"


def _skillhub_skills_dir() -> Path:
    return Path.home() / ".agents" / "skills"


class SkillHubError(Exception):
    """Raised when a SkillHub install/uninstall/sync operation fails."""


class SkillHubSkillNotInstalledError(SkillHubError):
    """The named skill is not installed from SkillHub (404, not a hub failure)."""


def _invalidate_skills_prompt_cache() -> None:
    try:
        from agent.prompt_builder import clear_skills_system_prompt_cache

        clear_skills_system_prompt_cache(clear_snapshot=True)
    except Exception:
        pass


def hub_status() -> dict[str, Any]:
    """Report the tenant SkillHub endpoint/credential configuration state."""
    from hermes_cli.config import load_config

    section = load_config().get("skill_hub") or {}
    url = str(section.get("url") or "").strip()
    api_key = str(section.get("api_key") or "").strip()
    return {
        "url": url,
        "configured": bool(url),
        "api_key_configured": bool(api_key),
    }


def _require_hub_config() -> tuple[str, str]:
    status = hub_status()
    if not status["configured"]:
        raise ValueError("SkillHub 未配置：请先在配置文件中设置 skill_hub.url")
    if not status["api_key_configured"]:
        raise ValueError("SkillHub 未配置凭据：请先在配置文件中设置 skill_hub.api_key")
    from hermes_cli.config import load_config

    api_key = str((load_config().get("skill_hub") or {}).get("api_key") or "").strip()
    return status["url"].rstrip("/"), api_key


def _ensure_external_dir_registered() -> None:
    """Make sure ``~/.agents/skills`` is scanned by the agent runtime."""
    from hermes_cli.config import load_config, save_config

    config = load_config()
    external_dirs = list((config.get("skills") or {}).get("external_dirs") or [])
    if _EXTERNAL_DIR_CONFIG_VALUE in external_dirs or str(_skillhub_skills_dir()) in external_dirs:
        return
    config.setdefault("skills", {})
    config["skills"]["external_dirs"] = external_dirs + [_EXTERNAL_DIR_CONFIG_VALUE]
    save_config(config)

    try:
        from agent.skill_utils import _external_dirs_cache_clear

        _external_dirs_cache_clear()
    except Exception:
        pass


def _ensure_skillhub_cli(url: str) -> Path:
    """Download and chmod the vendor's single-file skillhub CLI if missing."""
    binary = _skillhub_bin()
    if binary.is_file() and os.access(binary, os.X_OK):
        return binary

    import httpx

    binary.parent.mkdir(parents=True, exist_ok=True)
    try:
        response = httpx.get(f"{url}/api/v1/script/bash", timeout=_REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SkillHubError(f"下载 skillhub CLI 失败：{exc}") from exc

    binary.write_bytes(response.content)
    mode = binary.stat().st_mode
    binary.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return binary


def _run_skillhub_cli(args: list[str]) -> tuple[int, str]:
    url, api_key = _require_hub_config()
    binary = _ensure_skillhub_cli(url)
    env = {**os.environ, "SKILLHUB_URL": url, "SKILLHUB_API_KEY": api_key}
    try:
        proc = subprocess.run(
            [str(binary), *args],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=_REQUEST_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SkillHubError(f"skillhub CLI 执行超时：skillhub {' '.join(args)}") from exc
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode, _ANSI_ESCAPE_RE.sub("", output)


def install_skill(identifier: str) -> dict[str, Any]:
    """Install a skill from the tenant SkillHub by its catalog name."""
    identifier = (identifier or "").strip()
    if not identifier:
        raise ValueError("Skill 名称（identifier）不能为空")

    returncode, output = _run_skillhub_cli(["install", identifier])
    if returncode != 0:
        raise SkillHubError(output or f"安装 '{identifier}' 失败（exit {returncode}）")

    _ensure_external_dir_registered()
    _invalidate_skills_prompt_cache()
    return {"ok": True, "identifier": identifier, "output": output}


def sync_skill(name: str) -> dict[str, Any]:
    """Pull the latest version of a hub-installed skill (vendor CLI has no
    separate update command — install overwrites SKILL.md in place)."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Skill 名称不能为空")
    if not (_skillhub_skills_dir() / name / "SKILL.md").is_file():
        raise SkillHubSkillNotInstalledError(f"'{name}' 不是通过 SkillHub 安装的 skill，无法同步")

    returncode, output = _run_skillhub_cli(["install", name])
    if returncode != 0:
        raise SkillHubError(output or f"同步 '{name}' 失败（exit {returncode}）")

    _invalidate_skills_prompt_cache()
    return {"ok": True, "name": name, "output": output}


def uninstall_skill(name: str) -> dict[str, Any]:
    """Remove a hub-installed skill: delete its local directory.

    Vendor CLI has no uninstall endpoint, so this is a plain rmtree —
    matching the meeting's "从Skill目录里面删除就可以了" decision.
    """
    from tools.path_security import has_traversal_component

    name = (name or "").strip()
    if not name or has_traversal_component(name) or "/" in name:
        raise ValueError(f"非法的 Skill 名称: {name!r}")

    target = _skillhub_skills_dir() / name
    if not target.is_dir() or not (target / "SKILL.md").is_file():
        raise SkillHubSkillNotInstalledError(f"'{name}' 不是通过 SkillHub 安装的 skill")

    shutil.rmtree(target)
    _invalidate_skills_prompt_cache()
    return {"ok": True, "name": name}


def list_installed_skills() -> list[dict[str, Any]]:
    """List skills installed via the tenant SkillHub CLI — local state only,
    no network call (理解 A)."""
    skills_dir = _skillhub_skills_dir()
    if not skills_dir.is_dir():
        return []

    from datetime import datetime, timezone

    from tools.skills_tool import _parse_frontmatter

    entries: list[dict[str, Any]] = []
    for child in sorted(skills_dir.iterdir()):
        skill_md = child / "SKILL.md"
        if not child.is_dir() or not skill_md.is_file():
            continue
        description = ""
        try:
            content = skill_md.read_text(encoding="utf-8")
            frontmatter, _body = _parse_frontmatter(content[:4000])
            description = str(frontmatter.get("description", "")).strip()
        except Exception:
            pass
        try:
            mtime = skill_md.stat().st_mtime
            installed_at = datetime.fromtimestamp(mtime, tz=UTC).isoformat()
        except OSError:
            installed_at = ""
        entries.append(
            {
                "name": child.name,
                "description": description,
                "path": str(skill_md),
                "installed_at": installed_at,
            }
        )
    return entries


_CATALOG_LIST_KEYS = ("results", "skills", "items", "data")
_CATALOG_NAME_KEYS = ("name", "slug", "identifier", "id")
_CATALOG_DESC_KEYS = ("description", "summary", "desc")


def _extract_catalog_items(payload: Any) -> list[dict]:
    """Unwrap the vendor's catalog response into a plain list of dicts.

    The exact response shape is undocumented — tolerate a bare list or a
    dict wrapping the list under one of a few common keys.
    """
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in _CATALOG_LIST_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _normalize_catalog_entry(item: dict) -> dict[str, str] | None:
    """Pull a name/description pair out of one catalog entry.

    Returns ``None`` when no recognizable name field is present so the
    caller can skip (and log) the entry instead of showing a nameless card.
    """
    name = next((str(item[key]).strip() for key in _CATALOG_NAME_KEYS if item.get(key)), "")
    if not name:
        return None
    description = next((str(item[key]).strip() for key in _CATALOG_DESC_KEYS if item.get(key)), "")
    return {"name": name, "description": description}


def _fetch_catalog(search: str = "") -> list[dict[str, str]]:
    """Fetch the tenant SkillHub's skill catalog (not just locally-installed ones).

    Corresponds to the vendor CLI's ``skillhub list [search]``
    (``GET {url}/api/v1/skills/?search=...``, see module docstring). Response
    shape is undocumented, so entries with no recognizable name are skipped
    and logged rather than failing the whole request. Pagination shape is
    unknown too — only the first response is read; add follow-up-page
    handling once the real shape is known.
    """
    url, api_key = _require_hub_config()

    import httpx

    try:
        response = httpx.get(
            f"{url}/api/v1/skills/",
            params={"search": search} if search else None,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        raise SkillHubError(f"获取 SkillHub 目录失败：{exc}") from exc
    except ValueError as exc:
        raise SkillHubError(f"SkillHub 目录接口返回了无法解析的内容：{exc}") from exc

    items: list[dict[str, str]] = []
    for raw in _extract_catalog_items(payload):
        normalized = _normalize_catalog_entry(raw)
        if normalized is None:
            logger.warning("跳过一条无法识别名称字段的 SkillHub 目录条目: %r", raw)
            continue
        items.append(normalized)
    return items


def list_catalog(search: str = "") -> dict[str, Any]:
    """Merge the tenant SkillHub's remote catalog with local install state.

    Soft-fails on a remote error (hub not configured, unreachable, bad
    response) so the page still shows locally-installed skills instead of
    going blank — the caller sees this via ``catalog_available``.
    """
    installed_by_name = {entry["name"]: entry for entry in list_installed_skills()}

    catalog_error: str | None = None
    try:
        catalog_entries = _fetch_catalog(search)
    except (ValueError, SkillHubError) as exc:
        catalog_entries = []
        catalog_error = str(exc)

    merged: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for entry in catalog_entries:
        name = entry["name"]
        seen_names.add(name)
        local = installed_by_name.get(name)
        merged.append(
            {
                "name": name,
                "description": (local["description"] if local else "") or entry["description"],
                "installed": local is not None,
                "in_catalog": True,
                "installed_at": local["installed_at"] if local else None,
                "path": local["path"] if local else None,
            }
        )

    # Locally present but absent from this catalog response — e.g. a skill
    # dropped into ~/.agents/skills by something other than this tenant's
    # SkillHub. Surface it instead of quietly treating it as a hub member.
    for name, local in installed_by_name.items():
        if name in seen_names:
            continue
        merged.append(
            {
                "name": name,
                "description": local["description"],
                "installed": True,
                "in_catalog": False,
                "installed_at": local["installed_at"],
                "path": local["path"],
            }
        )

    merged.sort(key=lambda item: item["name"].lower())
    return {
        "skills": merged,
        "catalog_available": catalog_error is None,
        "catalog_error": catalog_error,
    }
