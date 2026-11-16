"""Cron job ownership by platform user (``identify``): who a job belongs to and who may see it.

A job created from a messaging turn records the creating user's ``{platform, user_id, user_name}``.
The scheduler restores that identity for the run (agent, prerun script, ``no_agent`` script) so the
owner's ``userenv`` values apply without gateway session vars, and the cron tools show and operate
on a job only for its owner. A legacy job without ``identify`` stays public; a malformed record is
never public.
"""
from __future__ import annotations

import contextlib
import functools
from typing import Any, Callable, Iterator, Optional, TypeVar

A = TypeVar("A")
R = TypeVar("R")


def build_job_identify(platform: Any, user_id: Any, user_name: Any = "") -> Optional[dict[str, str]]:
    """The ``identify`` record for a creating user, or None when there is no user id (CLI, cron)."""
    user_id_text = str(user_id or "").strip()
    if not user_id_text:
        return None
    return {
        "platform": str(platform or "").strip(),
        "user_id": user_id_text,
        "user_name": str(user_name or "").strip(),
    }


def parse_job_identify(value: Any) -> Optional[dict[str, str]]:
    """Validate a stored ``identify``: None for a public job, else the normalized record.

    Raises ``ValueError`` for anything else, so a hand-edited or corrupted owner can never read
    as public.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Cron job identify must be an object with platform, user_id, and user_name")
    platform, user_id, user_name = value.get("platform"), value.get("user_id"), value.get("user_name", "")
    if not isinstance(platform, str) or not isinstance(user_id, str) or not isinstance(user_name, str):
        raise ValueError("Cron job identify fields platform, user_id, and user_name must be strings")
    if not user_id.strip():
        raise ValueError("Cron job identify.user_id is required")
    return {"platform": platform.strip(), "user_id": user_id.strip(), "user_name": user_name.strip()}


def is_job_visible_to_identity(job: dict[str, Any], identity: Any = None) -> bool:
    """Whether the caller (a ``tools.user_env_runtime.UserEnvIdentity`` or None) may see *job*.

    Public (legacy) jobs are visible to everyone; owned jobs only to the same platform user;
    malformed owners to nobody.
    """
    try:
        identify = parse_job_identify(job.get("identify"))
    except ValueError:
        return False
    if identify is None:
        return True
    if identity is None:
        return False
    return (identify["platform"] == str(getattr(identity, "platform", "") or "").strip()
            and identify["user_id"] == str(getattr(identity, "user_id", "") or "").strip())


def job_owner(job: dict[str, Any]) -> Optional[dict[str, str]]:
    """The owner a run must act as, None for a public job; a malformed owner fails the run instead
    of silently running as public."""
    try:
        return parse_job_identify(job.get("identify"))
    except ValueError as exc:
        raise RuntimeError(f"Cron job '{job.get('id')}' has malformed identify: {exc}") from exc


@contextlib.contextmanager
def acting_as_owner(job: dict[str, Any]) -> Iterator[Optional[dict[str, str]]]:
    """Bind the job owner's ``userenv`` identity for the block (no-op for a public job).

    Script envs come from ``build_subprocess_env``, which overlays the bound user's env, and the
    binding is always undone, so a scheduler thread never carries one owner into the next job.
    """
    owner = job_owner(job)
    if owner is None:
        yield None
        return
    from tools.user_env_runtime import bind_current_user_env_identity
    with bind_current_user_env_identity(owner["platform"], owner["user_id"], owner["user_name"]):
        yield owner


def runs_as_job_owner(run_job: Callable[..., R]) -> Callable[..., R]:
    """Decorate ``cron.scheduler.run_job`` so the whole fire (prerun or ``no_agent`` script, agent,
    tools) acts as the job's owner."""
    @functools.wraps(run_job)
    def run_as_owner(job: dict[str, Any], *args: Any, **kwargs: Any) -> R:
        with acting_as_owner(job):
            return run_job(job, *args, **kwargs)
    return run_as_owner


def as_job_owner(job: dict[str, Any], agent: A) -> A:
    """Make a freshly built cron agent act for the job's owner (``platform`` stays ``cron``).

    Every tool call re-binds the user identity from the agent (``call_as_agent_user``), so the
    agent itself must carry the owner; ``_user_env_platform`` keeps the owner's platform for the
    env store while cron-specific behaviour keyed on ``platform == "cron"`` is unchanged.
    """
    owner = job_owner(job)
    if owner is not None:
        agent._user_id, agent._user_name = owner["user_id"], owner["user_name"]
        agent._user_env_platform = owner["platform"] or "cron"
    return agent


def apply_identify_field(record: dict[str, Any]) -> None:
    """Normalize ``record["identify"]`` for readers; a malformed owner is flagged, not dropped. A
    public job carries no key at all, so its records stay byte-identical to pre-ownership jobs."""
    if "identify" not in record:
        return
    try:
        record["identify"] = parse_job_identify(record.get("identify"))
    except ValueError:
        record["_identify_error"] = "malformed identify"
