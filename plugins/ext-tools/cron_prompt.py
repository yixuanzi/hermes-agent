"""cron_prompt — 返回计划任务的全量 prompt（带 Owner 身份严格校验）。

设计要点：
  - 身份来源是 tools.user_env_runtime.get_current_user_env_identity()（由
    tool_executor 在每次工具调用前绑定），不取自 LLM 提供的参数 —— 调用者
    身份不可伪造。
  - 校验规则（无任何角色豁免，admin 同样受检）：
      * job 带 identify 字段 → 调用者 (platform, user_id) 必须与
        identify 严格相等才返回 prompt；否则拒绝且不回显任何 prompt 片段。
      * job 无 identify 字段 → 视为无主任务，直接返回。
  - 复用 cron.jobs 现成的 resolve_job_ref / parse_job_identify /
    is_job_visible_to_identity，与 cronjob 工具的可见性语义保持一致。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cron.jobs import (  # noqa: E402
    AmbiguousJobReference,
    is_job_visible_to_identity,
    parse_job_identify,
    resolve_job_ref,
)


def _current_identity():
    """当前调用者身份（ContextVar，由 tool_executor 绑定；LLM 无法伪造）。"""
    try:
        from tools.user_env_runtime import get_current_user_env_identity

        return get_current_user_env_identity()
    except Exception:
        return None


def _caller_label(identity) -> str:
    if identity is None:
        return "unknown"
    return f"{identity.platform}:{identity.user_id}"


def _owner_label(identify: dict) -> str:
    return f"{identify.get('platform', '?')}:{identify.get('user_id', '?')}"


def cron_prompt(job_id: str = "") -> str:
    """按任务 ID 或名称取回计划任务的全量 prompt（先做 Owner 身份校验）。"""
    ref = str(job_id or "").strip()
    if not ref:
        return json.dumps(
            {"success": False, "error": "job_id is required (job ID or name)"},
            ensure_ascii=False,
        )

    # 1. 定位任务（ID 优先，名称不区分大小写；重名抛 AmbiguousJobReference）
    try:
        job = resolve_job_ref(ref)
    except AmbiguousJobReference as exc:
        return json.dumps(
            {"success": False, "error": str(exc)}, ensure_ascii=False
        )
    if job is None:
        return json.dumps(
            {"success": False, "error": f"cron job not found: {ref}"},
            ensure_ascii=False,
        )

    # 2. 身份校验（取回数据前完成；无任何角色豁免）
    identity = _current_identity()
    try:
        identify = parse_job_identify(job.get("identify"))
    except ValueError as exc:
        # identify 字段存在但格式非法 —— 视为不可归属，fail-closed 拒绝
        return json.dumps(
            {
                "success": False,
                "error": f"job identify field is invalid: {exc}",
                "job_id": job.get("id"),
            },
            ensure_ascii=False,
        )

    if identify is not None:
        if not is_job_visible_to_identity(job, identity):
            return json.dumps(
                {
                    "success": False,
                    "error": "permission denied: caller is not the job owner",
                    "job_id": job.get("id"),
                    "owner": _owner_label(identify),
                    "caller": _caller_label(identity),
                },
                ensure_ascii=False,
            )
    # identify 为 None（无身份字段）→ 直接放行

    # 3. 返回全量 prompt + 元信息
    return json.dumps(
        {
            "success": True,
            "job_id": job.get("id"),
            "name": job.get("name"),
            "schedule": job.get("schedule_display") or "?",
            "state": job.get("state"),
            "owner": _owner_label(identify) if identify else None,
            "caller": _caller_label(identity),
            "prompt": job.get("prompt") or "",
        },
        ensure_ascii=False,
    )


# ------------------------------------------------------------------ 注册 ----

_SCHEMA = {
    "name": "cron_prompt",
    "description": (
        "按任务 ID 或名称返回计划任务（cron job）的全量 prompt。"
        "仅任务 Owner 本人可读取（admin 亦不豁免）；任务无身份字段时直接返回。"
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
