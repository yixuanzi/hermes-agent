"""Cron service adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
import threading

from fastapi import HTTPException
from hermes_state import SessionDB


_CRON_PROFILE_LOCK = threading.RLock()
_RAW_DETAIL_RESPONSE_ONLY_FIELDS = frozenset({"profile_name", "hermes_home", "is_default_profile"})


def _profile_to_dict(profile_obj: Any) -> dict[str, Any]:
    return {
        "name": getattr(profile_obj, "name", "") or "default",
    }


def _fallback_profile_dicts(profiles_mod: Any) -> list[dict[str, Any]]:
    profiles_root = Path(profiles_mod.PROFILES_DIR)
    rows = [{"name": "default"}]
    if profiles_root.exists():
        for child in profiles_root.iterdir():
            if child.is_dir() and (child / "profile.yaml").exists():
                rows.append({"name": child.name})
    return rows


def _cron_profile_dicts() -> list[dict[str, Any]]:
    from hermes_cli import profiles as profiles_mod

    try:
        return [_profile_to_dict(p) for p in profiles_mod.list_profiles()]
    except Exception:
        return _fallback_profile_dicts(profiles_mod)


def _cron_profile_home(profile: Optional[str]) -> tuple[str, Path]:
    from hermes_cli import profiles as profiles_mod

    raw = (profile or "default").strip() or "default"
    try:
        canon = profiles_mod.normalize_profile_name(raw)
        profiles_mod.validate_profile_name(canon)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not profiles_mod.profile_exists(canon):
        raise HTTPException(status_code=404, detail=f"Profile '{canon}' does not exist.")
    return canon, profiles_mod.get_profile_dir(canon)


def get_runtime_profile_name() -> str:
    """Resolve the startup/runtime profile used by the current AISOC process."""
    from hermes_cli import profiles as profiles_mod

    try:
        raw = str(profiles_mod.get_active_profile_name() or "default").strip() or "default"
        # print(f"current active profile: '{raw}'")
    except Exception:
        return "default"

    if raw == "custom":
        return "default"

    try:
        canon = profiles_mod.normalize_profile_name(raw)
        profiles_mod.validate_profile_name(canon)
    except ValueError:
        return "default"

    if canon != "default" and not profiles_mod.profile_exists(canon):
        return "default"
    return canon


def _annotate_cron_job(job: dict[str, Any], profile: str, home: Path) -> dict[str, Any]:
    annotated = dict(job)
    annotated["profile"] = profile
    annotated["profile_name"] = profile
    annotated["hermes_home"] = str(home)
    annotated["is_default_profile"] = profile == "default"
    return annotated


def _call_cron_for_profile(target_profile: Optional[str], func_name: str, *args, **kwargs):
    profile_name, home = _cron_profile_home(target_profile)
    with _CRON_PROFILE_LOCK:
        from cron import jobs as cron_jobs

        old_cron_dir = cron_jobs.CRON_DIR
        old_jobs_file = cron_jobs.JOBS_FILE
        old_output_dir = cron_jobs.OUTPUT_DIR
        cron_jobs.CRON_DIR = home / "cron"
        cron_jobs.JOBS_FILE = cron_jobs.CRON_DIR / "jobs.json"
        cron_jobs.OUTPUT_DIR = cron_jobs.CRON_DIR / "output"
        try:
            result = getattr(cron_jobs, func_name)(*args, **kwargs)
        finally:
            cron_jobs.CRON_DIR = old_cron_dir
            cron_jobs.JOBS_FILE = old_jobs_file
            cron_jobs.OUTPUT_DIR = old_output_dir

    if isinstance(result, list):
        return [_annotate_cron_job(j, profile_name, home) for j in result]
    if isinstance(result, dict):
        return _annotate_cron_job(result, profile_name, home)
    return result


def _find_cron_job_profile(job_id: str) -> str | None:
    for profile in _cron_profile_dicts():
        name = str(profile.get("name") or "")
        if not name:
            continue
        jobs = _call_cron_for_profile(name, "list_jobs", True)
        if any(j.get("id") == job_id or j.get("name") == job_id for j in jobs):
            return name
    return None


def list_jobs(profile: str | None = None) -> list[dict[str, Any]]:
    requested = (profile or get_runtime_profile_name()).strip() or get_runtime_profile_name()
    if requested.lower() != "all":
        return _call_cron_for_profile(requested, "list_jobs", True)

    jobs = []
    for item in _cron_profile_dicts():
        name = str(item.get("name") or "")
        if not name:
            continue
        jobs.extend(_call_cron_for_profile(name, "list_jobs", True))
    return jobs


def get_job(job_id: str, profile: str | None = None):
    selected = profile or _find_cron_job_profile(job_id)
    if not selected:
        return None
    return _call_cron_for_profile(selected, "get_job", job_id)


def create_job(
    profile: str,
    *,
    prompt: str,
    schedule: str,
    name: str,
    deliver: str,
    skills: list[str] | None = None,
    skill: str | None = None,
    enabled_toolsets: list[str] | None = None,
    model: str | None = None,
    provider: str | None = None,
    base_url: str | None = None,
    script: str | None = None,
    workdir: str | None = None,
    no_agent: bool = False,
):
    return _call_cron_for_profile(
        profile,
        "create_job",
        prompt=prompt,
        schedule=schedule,
        name=name,
        deliver=deliver,
        skills=skills,
        skill=skill,
        enabled_toolsets=enabled_toolsets,
        model=model,
        provider=provider,
        base_url=base_url,
        script=script,
        workdir=workdir,
        no_agent=no_agent,
        profile=profile,
    )


def update_job(job_id: str, updates: dict, profile: str | None = None):
    selected = profile or _find_cron_job_profile(job_id)
    if not selected:
        return None
    return _call_cron_for_profile(selected, "update_job", job_id, updates)


def _normalize_raw_identify(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="identify must be an object or null")

    from cron.jobs import build_job_identify, parse_job_identify

    normalized = build_job_identify(
        value.get("platform"),
        value.get("user_id"),
        value.get("user_name"),
    )
    if normalized is None or parse_job_identify(normalized) is None:
        raise HTTPException(
            status_code=400,
            detail="identify must include string platform, user_id, and user_name fields",
        )
    return normalized


def update_job_raw(job_id: str, job: dict[str, Any], profile: str | None = None):
    if not isinstance(job, dict):
        raise HTTPException(status_code=400, detail="job must be a JSON object")

    current = get_job(job_id, profile=profile)
    if not current:
        return None

    provided_id = job.get("id")
    if provided_id is not None and str(provided_id) != str(current.get("id") or job_id):
        raise HTTPException(status_code=400, detail="Cron job id cannot be changed via raw update")

    sanitized = dict(job)
    for field in _RAW_DETAIL_RESPONSE_ONLY_FIELDS:
        sanitized.pop(field, None)

    current_profile = current.get("profile")
    if "profile" in sanitized and sanitized.get("profile") != current_profile:
        raise HTTPException(status_code=400, detail="Cron job profile cannot be changed via raw update")

    if "identify" in sanitized:
        sanitized["identify"] = _normalize_raw_identify(sanitized.get("identify"))

    current_clean = {k: v for k, v in current.items() if k not in _RAW_DETAIL_RESPONSE_ONLY_FIELDS}
    updates = {
        key: value
        for key, value in sanitized.items()
        if current_clean.get(key) != value or key not in current_clean
    }

    return update_job(job_id, updates, profile=profile)


def pause_job(job_id: str, profile: str | None = None):
    selected = profile or _find_cron_job_profile(job_id)
    if not selected:
        return None
    return _call_cron_for_profile(selected, "pause_job", job_id)


def resume_job(job_id: str, profile: str | None = None):
    selected = profile or _find_cron_job_profile(job_id)
    if not selected:
        return None
    return _call_cron_for_profile(selected, "resume_job", job_id)


def trigger_job(job_id: str, profile: str | None = None):
    selected = profile or _find_cron_job_profile(job_id)
    if not selected:
        return None
    return _call_cron_for_profile(selected, "trigger_job", job_id)


def remove_job(job_id: str, profile: str | None = None) -> bool:
    selected = profile or _find_cron_job_profile(job_id)
    if not selected:
        return False
    return bool(_call_cron_for_profile(selected, "remove_job", job_id))


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _cron_session_pattern(job_id: str) -> str:
    return f"cron_{_escape_like(job_id)}%"


def get_job_history(job_id: str, profile: str | None = None) -> list[dict[str, Any]] | None:
    try:
        job = get_job(job_id, profile=profile)
    except Exception:
        return []

    if not job:
        return None

    db = SessionDB()
    try:
        with db._lock:
            rows = db._conn.execute(
                """
                SELECT id, started_at, ended_at, message_count,
                       COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0) AS total_tokens,
                       end_reason
                FROM sessions
                WHERE id LIKE ? ESCAPE '\\'
                ORDER BY started_at DESC
                LIMIT 20
                """,
                (_cron_session_pattern(job_id),),
            ).fetchall()
        records = [dict(row) for row in rows]
    finally:
        db.close()

    history: list[dict[str, Any]] = []
    for row in records:
        started_at = row.get("started_at")
        ended_at = row.get("ended_at")
        duration = None
        if started_at is not None and ended_at is not None:
            try:
                duration = int(float(ended_at) - float(started_at))
            except Exception:
                duration = None
        history.append(
            {
                "session_id": str(row.get("id") or ""),
                "started_at": started_at,
                "ended_at": ended_at,
                "duration_seconds": duration,
                "messages": int(row.get("message_count") or 0),
                "tokens": int(row.get("total_tokens") or 0),
                "status": str(row.get("end_reason") or "completed"),
            }
        )

    return history
