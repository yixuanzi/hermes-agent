"""Which cron jobs the calling platform user may see and operate through the ``cronjob`` tool.

Ownership is the job's ``identify`` (cron/jobs_identify.py). The caller is the ``userenv`` identity
bound for the tool call (agent/tool_executor.py binds it from the agent), never anything the model
passes, so one messaging user can neither list nor touch another user's jobs. Legacy jobs without an
owner stay visible to everyone. Event/webhook triggers have no calling user and do not use this.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Optional

from cron.jobs import AmbiguousJobReference, get_job
from cron.jobs_identify import build_job_identify, is_job_visible_to_identity


def caller_identity() -> Any:
    """The platform user this tool call acts for, or None (CLI, cron without an owner)."""
    from tools.user_env_runtime import get_current_user_env_identity
    return get_current_user_env_identity()


def caller_identify() -> Optional[dict[str, str]]:
    """The ``identify`` record a job created by this caller is owned by."""
    identity = caller_identity()
    if identity is None:
        return None
    return build_job_identify(identity.platform, identity.user_id, identity.user_name)


def visible_jobs(jobs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    identity = caller_identity()
    return [job for job in jobs if is_job_visible_to_identity(job, identity)]


def get_visible_job(job_id: str) -> Optional[dict[str, Any]]:
    job = get_job(job_id)
    return job if job is not None and is_job_visible_to_identity(job, caller_identity()) else None


def resolve_visible_job_ref(ref: str, resolve: Callable[[str], Optional[dict[str, Any]]]) -> Optional[dict[str, Any]]:
    """``resolve(ref)`` (ID, then name) narrowed to the caller's jobs.

    An ambiguous name is re-judged over the caller's matches only, so the error never lists (and a
    unique visible match is never blocked by) another user's job.
    """
    identity = caller_identity()
    try:
        job = resolve(ref)
    except AmbiguousJobReference as exc:
        visible = [match for match in exc.matches if is_job_visible_to_identity(match, identity)]
        if len(visible) > 1:
            raise AmbiguousJobReference(ref, visible) from None
        return visible[0] if visible else None
    return job if job is not None and is_job_visible_to_identity(job, identity) else None
