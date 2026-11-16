"""cron_prompt — 返回计划任务的全量 prompt（仅限调用者自己的任务与无主任务）。

设计要点：
  - 调用者身份是 tool_executor 在每次工具调用前绑定的平台用户（tools.cronjob_visibility
    .caller_identity），不取自 LLM 提供的参数 —— 调用者身份不可伪造。
  - 可见性与 cronjob 工具完全一致（tools.cronjob_visibility.resolve_caller_job）：无任何
    角色豁免（admin 同样受检）；别人的任务、属主记录损坏的任务对调用者而言就是不存在，
    既不回显 prompt 片段，也不透露任务是否存在、归谁所有；重名只在调用者自己的任务里判定。
"""

from __future__ import annotations

import json


def _current_identity():
    """当前调用者身份（ContextVar，由 tool_executor 绑定；LLM 无法伪造）。"""
    from tools.cronjob_visibility import caller_identity

    return caller_identity()


def _resolve_job(ref: str):
    from tools.cronjob_visibility import resolve_caller_job

    return resolve_caller_job(ref)


def _caller_label(identity) -> str:
    if identity is None:
        return "unknown"
    return f"{identity.platform}:{identity.user_id}"


def _owner_label(job: dict):
    identify = job.get("identify")
    if not isinstance(identify, dict):
        return None
    return f"{identify.get('platform', '?')}:{identify.get('user_id', '?')}"


def _error(message: str) -> str:
    return json.dumps({"success": False, "error": message}, ensure_ascii=False)


def cron_prompt(job_id: str = "") -> str:
    """按任务 ID 或名称取回调用者可见的计划任务的全量 prompt。"""
    ref = str(job_id or "").strip()
    if not ref:
        return _error("job_id is required (job ID or name)")
    try:
        # ID 优先，名称不区分大小写；只在调用者可见的任务里定位，重名抛 AmbiguousJobReference
        job = _resolve_job(ref)
    except LookupError as exc:
        return _error(str(exc))
    if job is None:
        return _error(f"cron job not found: {ref}")
    return json.dumps(
        {
            "success": True,
            "job_id": job.get("id"),
            "name": job.get("name"),
            "schedule": job.get("schedule_display") or "?",
            "state": job.get("state"),
            "owner": _owner_label(job),
            "caller": _caller_label(_current_identity()),
            "prompt": job.get("prompt") or "",
        },
        ensure_ascii=False,
    )


# ------------------------------------------------------------------ 注册 ----

_SCHEMA = {
    "name": "cron_prompt",
    "description": (
        "按任务 ID 或名称返回计划任务（cron job）的全量 prompt。"
        "仅能读取调用者自己的任务与无主任务（admin 亦不豁免）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "job_id": {
                "type": "string",
                "description": "计划任务的 ID（如 50dafdcd3ff6）或名称（重名会报错）",
            },
        },
        "required": ["job_id"],
    },
}


def register(ctx):
    ctx.register_tool(
        name="cron_prompt",
        toolset="ext",
        schema=_SCHEMA,
        handler=lambda args, **kwargs: cron_prompt(job_id=args.get("job_id", "")),
    )
