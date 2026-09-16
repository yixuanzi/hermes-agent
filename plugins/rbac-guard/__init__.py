"""rbac-guard — Hermes RBAC 插件。

双层防御架构（对应用户场景）：
  1. prompt 逻辑层（软约束）：pre_llm_call 钩子在每轮开始时拿到
     platform + sender_id，查角色表，把该角色的权限约束块注入本轮
     user message（Hermes 设计上不允许插件改 system prompt —— 为保护
     prompt cache，上下文注入永远落在 user message，效果等同：模型
     在生成任何回复/工具调用前都会先看到这段约束）。
  2. 代码执行层（硬控制）：pre_tool_call 钩子在每次工具执行前拿到
     tool_name + args，按当前用户角色决定 放行 / block / 升级人工审批。
     block 指令由 Hermes core 的 resolve_pre_tool_block() 统一执行，
     fail-closed —— 这是真正的安全边界，不依赖模型自觉。

session_key 形如 agent:main:feishu:group:oc_xxx / agent:main:telegram:dm:123，
从中解析出 platform 与 user_id；CLI 会话没有身份，按 local:local 处理。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import roles  # noqa: E402

_VALID_ROLES = tuple(roles.PERSISTED_ROLES)

# session_key 例：agent:main:feishu:group:oc_xxx / agent:main:whatsapp:dm:1555...
_PLATFORM_RE = re.compile(r"agent:[^:]+:([A-Za-z0-9_\-]+):(group|channel|dm|thread):(.+)")


# ---------------------------------------------------------------- 身份解析 ----
# session_id:turn_id -> (platform, uid)，由每轮 pre_llm_call 写入。
# 群聊场景下 session_key 只含群 ID，工具层必须用这里缓存的个人身份判定，
# 保证 prompt 层与执行层看到的是同一个用户；不能只用 session_id，
# 否则同一个共享 session 的不同用户会互相覆盖身份。
_IDENTITY_CACHE: dict[str, tuple[str, str]] = {}
_IDENTITY_CACHE_MAX = 512


def _identity_cache_key(session_id: str = "", turn_id: str = "") -> str:
    """Build the only cache key used for turn identity mappings."""
    session = str(session_id or "").strip()
    turn = str(turn_id or "").strip()
    if not session or not turn:
        return ""
    return f"{session}:{turn}"


def _remember_identity(
    session_id: str,
    turn_id: str,
    platform: str,
    sender_id: str,
) -> None:
    cache_key = _identity_cache_key(session_id, turn_id)
    if cache_key and (platform or sender_id):
        if len(_IDENTITY_CACHE) >= _IDENTITY_CACHE_MAX:
            _IDENTITY_CACHE.clear()  # 简单防膨胀
        _IDENTITY_CACHE[cache_key] = (platform or "cli", sender_id or "local")


def resolve_identity(session_id: str, turn_id: str = "") -> tuple[str, str]:
    """优先取本轮 ``session_id:turn_id`` 缓存的个人身份。

    没有 turn_id 时不读取 session 级别的旧缓存，避免共享 session 中发生
    身份串用；此时仅使用 session key 的兼容性解析结果。
    """
    cache_key = _identity_cache_key(session_id, turn_id)
    hit = _IDENTITY_CACHE.get(cache_key) if cache_key else None
    if hit:
        return hit
    return parse_identity(session_key=session_id)


def parse_identity(session_key: str, platform: str = "", sender_id: str = "") -> tuple[str, str]:
    """从 hook kwargs 中尽力解析 (platform, user_id)。

    pre_llm_call 直接给 platform + sender_id，最可靠；
    pre_tool_call 只给 session_id/session_key，用正则兜底解析。
    """
    if platform or sender_id:
        return (platform or "cli"), (sender_id or "local")
    sk = session_key or ""
    m = _PLATFORM_RE.match(sk)
    if m:
        return m.group(1).lower(), m.group(3)
    return "cli", "local"


# ------------------------------------------------------------------- hooks ----
def on_pre_llm_call(session_id="", platform="", sender_id="", turn_id="", **kwargs):
    """每轮 LLM 调用前：查角色 → 注入角色约束块（软约束层）。

    返回 {"context": ...} 会被 Hermes 注入本轮 user message 顶部；
    这也是“任务开始时获取用户身份”的挂载点。
    """
    plat, uid = (platform or "cli"), (sender_id or "local")
    _remember_identity(session_id, turn_id, plat, uid)
    block = roles.prompt_block(plat, uid)
    roles.audit("context_injected", identity=roles.identity_key(plat, uid),
                session_id=session_id)
    return {"context": block}


def on_pre_tool_call(tool_name="", args=None, session_id="", turn_id="", **kwargs):
    """每次工具执行前：角色硬控制 —— 放行 / block / 升级审批。

    返回值契约（hermes_cli.plugins._get_pre_tool_call_directive_details）：
      {"action": "block", "message": "..."}   → 工具被否决，message 成为 tool result
      {"action": "approve", "message": "..."} → 升级到人工审批门，拒绝/超时=拒绝
      其他/None                               → 放行
    审批门本身 fail-closed：gate 出错也会变成 block。
    """
    plat, uid = resolve_identity(session_id, turn_id)
    role_name = roles.role_for(plat, uid)
    role = roles.get_role(plat, uid)

    if not roles.tool_allowed(role, tool_name):
        roles.audit("tool_blocked", identity=roles.identity_key(plat, uid),
                    role=role_name, tool=tool_name,
                    args=json.dumps(args or {}, ensure_ascii=False)[:500],
                    reason="tool is not allowed")
        return {
            "action": "block",
            "message": (
                f"[RBAC] 已拒绝：当前角色 '{role_name}' 无权调用工具 '{tool_name}'。"
                f"请联系管理员调整角色。"
        ),
    }

    # Parameter rules are a hard allow condition and must pass before a
    # dangerous operation can be sent to the human approval gate.
    params_allowed, reason = roles.tool_params_allowed(role, tool_name, args)
    if not params_allowed:
        roles.audit("tool_blocked", identity=roles.identity_key(plat, uid),
                    role=role_name, tool=tool_name,
                    args=json.dumps(args or {}, ensure_ascii=False)[:500],
                    reason=reason)
        return {
            "action": "block",
            "message": (
                f"[RBAC] 已拒绝：工具 '{tool_name}' 的参数执行不满足当前角色的约束。"
                # f"（{reason}）"
            ),
        }

    # Non-admin dangerous operations still require human approval.
    dangerous = _looks_dangerous(tool_name, args or {})
    if dangerous and role_name in roles.PERSISTED_ROLES:
        roles.audit("escalate_approval", identity=roles.identity_key(plat, uid),
                    role=role_name, tool=tool_name)
        return {
            "action": "approve",
            "message": f"RBAC: 角色 {role_name} 请求执行危险工具 {tool_name}，需人工确认",
        }

    return None  # 放行


def _looks_dangerous(tool_name: str, args: dict) -> bool:
    if tool_name in ("terminal", "execute_code", "computer_use"):
        blob = json.dumps(args, ensure_ascii=False)
        return bool(roles.DANGEROUS_PATTERN.search(blob))
    return False


def on_post_tool_call(
    tool_name="", args=None, result="", session_id="", turn_id="", **kwargs
):
    """观察者：审计日志（返回值被忽略）。"""
    plat, uid = resolve_identity(session_id, turn_id)
    roles.audit("tool_executed", identity=roles.identity_key(plat, uid),
                tool=tool_name, args=json.dumps(args or {}, ensure_ascii=False)[:500], ok=("error" not in str(result).lower()[:80]))


# ------------------------------------------------------- 管理工具（给 LLM） ----
def _tool_rbac_status(params, **kwargs):
    plat = (params or {}).get("platform") or "cli"
    uid = (params or {}).get("user_id") or "local"
    role_name = roles.role_for(plat, uid)
    r = roles.get_role(plat, uid)
    return json.dumps({
        "success": True,
        "identity": roles.identity_key(plat, uid),
        "role": role_name,
        "summary": r["summary"],
        "prompt_constraints": r["prompt_constraints"],
        "allow_tools": r["allow_tools"] if r["allow_tools"] is not None else "all (deny-list mode)",
        "denied_tools": sorted(r.get("denied_tools", [])),
        "tools_paras": r.get("tools_paras", {}),
        "dangerous_pattern": roles.DANGEROUS_PATTERN.pattern,
        "known_roles": list(roles.PERSISTED_ROLES),
    }, ensure_ascii=False)


def _tool_rbac_set_role(params, **kwargs):
    p = params or {}
    # 安全设计：只能改目标身份的角色，不能改自己的判定身份——
    # 自己的身份永远来自 hook 注入的 platform/user_id，不由 LLM 提供。
    plat = (p.get("platform") or "cli").strip().lower()
    uid = (p.get("user_id") or "local").strip()
    role = (p.get("role") or "").strip()
    if role not in roles.PERSISTED_ROLES:
        return json.dumps({
            "success": False,
            "error": f"unsupported role '{role}'. valid: {list(roles.PERSISTED_ROLES)}",
        }, ensure_ascii=False)
    ok = roles.set_role(plat, uid, role)
    return json.dumps({"success": ok, "identity": roles.identity_key(plat, uid),
                       "role": role if ok else None}, ensure_ascii=False)


# ------------------------------------------------------------------ 注册 ----
def register(ctx):
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)

    # ctx.register_tool(
    #     name="rbac_status",
    #     toolset="rbac",
    #     schema={
    #         "name": "rbac_status",
    #         "description": (
    #             "查询 RBAC 角色信息：给定 platform + user_id，返回其角色、"
    #             "权限约束、工具白/黑名单。"
    #         ),
    #         "parameters": {
    #             "type": "object",
    #             "properties": {
    #                 "platform": {"type": "string", "description": "平台名，如 feishu/telegram/cli"},
    #                 "user_id": {"type": "string", "description": "平台用户 ID"},
    #             },
    #             "required": [],
    #         },
    #     },
    #     handler=_tool_rbac_status,
    # )

    ctx.register_tool(
        name="rbac_set_role",
        toolset="rbac",
        schema={
            "name": "rbac_set_role",
            "description": (
                "需要operator以上角色:为 platform:user_id 设置 RBAC 角色(admin,operator,user)。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "platform": {"type": "string", "description": "平台名"},
                    "user_id": {"type": "string", "description": "平台用户 ID"},
                    "role": {"type": "string", "enum": list(_VALID_ROLES)},
                },
                "required": ["platform", "user_id", "role"],
            },
        },
        handler=_tool_rbac_set_role,
    )
