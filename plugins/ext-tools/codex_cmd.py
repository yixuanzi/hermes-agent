"""codex_cmd — /codex slash 命令：把当前会话切到本地 codex app-server 执行。

/codex on [cwd=DIR] [sandbox=read|network|full]
                      当前会话下一条消息起，整轮交给本地 codex CLI（codex app-server）
/codex off            恢复 Hermes 默认 loop
/codex status         查看当前会话状态
/codex [help]         命令帮助（不带参数时默认显示）

设计要点：
  - 会话级：开关状态按 Hermes session_id 记在本插件内存里，不写 config.yaml，
    不改 ~/.codex/config.toml，不迁移 MCP。codex 子进程直接使用本机已配置好的
    codex CLI（模型、鉴权、sandbox、MCP 均取自 ~/.codex）。/new 等换会话后自动失效。
  - 复用现有 runtime：core 在 run_conversation() 里若
    ``agent.api_mode == "codex_app_server"`` 就调用
    agent/codex_runtime.run_codex_app_server_turn()（事件桥接到 CLI/gateway 界面、
    消息投影入库、token 统计、审批桥接、中断/steer）。本插件只在每轮
    ``pre_llm_call`` 钩子里（codex 分支判断之前、agent 线程上）把当前 agent 的
    api_mode 切过去/切回来。当前 agent 通过
    agent.subagent_lifecycle.get_active_subagent_parent() 取得——run_agent 在每次
    run_conversation 外层都用 bind_subagent_parent(self) 绑定了它。
    只在轮次边界切换，绝不在运行中的轮次里改 api_mode。
  - codex 线程归会话所有：CodexAppServerSession 由本插件经
    attach_codex_app_server_session() 创建并记在会话状态里，而不是随 AIAgent 生灭。
    gateway 会因 agent 缓存重建（群聊换人、空闲驱逐、跨进程写入等）换一个新的
    AIAgent，本插件把同一个 codex 线程重新绑定到新 agent，同一 Hermes 会话内
    codex 上下文连续；off / 会话结束时关闭线程。
  - 宿主运行时保真：切换前把 api_mode / session_cwd 快照挂在 agent 实例上，off
    时原样恢复；codex 模式期间辅助调用（标题生成、后台 memory review fork、压缩
    可行性检查）仍按宿主 api_mode 路由，而不是被 core 的
    codex_app_server→codex_responses 降级误伤非 Responses 协议的 provider。
  - 会话识别（命令执行时）：CLI 用插件管理器的 _cli_ref；gateway 用本插件的
    pre_gateway_dispatch 钩子在同一 _handle_message 协程里捕获 (runner, source)，
    并与 gateway 绑定的 userenv 调用者身份交叉校验；TUI 仅在进程内只有一个会话时可用。
  - 安全：codex 工具在子进程内执行，Hermes 的 rbac-guard pre_tool_call 管不到。
    因此 gateway 下 rbac-guard 启用时只有 admin 能开启；共享会话（群聊）里只有
    开启者本人的消息走 codex，其他成员仍走 Hermes loop（受 RBAC 约束）。
  - 沙箱档位：sandbox=read|network|full（默认 network：工作区可写 + 网络开启）经
    thread/start 的 sandbox / approvalPolicy / config 字段按线程指定，不改 ~/.codex；
    full 为无沙箱且从不审批，回显与 status 醒目警示。
  - 权限可见：/codex on 时即启动本会话的 codex 线程（第一轮直接复用），把 codex 在
    thread/start 上报告的实际权限档、沙箱、网络与审批策略回显给用户；/codex status
    同样展示。启动放在线程里执行，gateway 事件循环不被阻塞。
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import shlex
import sys
import threading
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

logger = logging.getLogger(__name__)

CODEX_API_MODE = "codex_app_server"

# sandbox= 档位 → thread/start 覆盖字段（按线程生效，不写 ~/.codex/config.toml）
SANDBOX_MODES: dict[str, dict] = {
    "read": {"sandbox": "read-only"},
    "network": {
        "sandbox": "workspace-write",
        "config": {"sandbox_workspace_write": {"network_access": True}},
    },
    "full": {"sandbox": "danger-full-access", "approvalPolicy": "never"},
}
DEFAULT_SANDBOX_MODE = "network"
_SANDBOX_MODE_LABELS = {
    "read": "只读沙箱，网络关闭",
    "network": "工作区可写，网络开启",
    "full": "⚠️ 无沙箱，网络不受限，从不审批",
}
_NO_SANDBOX_WARNING = (
    "⚠️ **无沙箱**：codex 以当前系统账号直接执行任意命令，可读写删除该账号能访问的任何文件，"
    "网络不受限，且不会请求任何审批（Hermes 审批与 rbac-guard 均不生效）。"
)


def thread_params_for(sandbox_mode: str) -> dict:
    return SANDBOX_MODES[sandbox_mode]
# 首次切到 codex 前的宿主运行时快照，挂在 agent 实例上；off 时据此恢复。
_HOST_ATTR = "_ext_codex_host"
_MISSING = object()


@dataclass
class CodexMode:
    session_id: Optional[str]
    cwd: Optional[str] = None
    # gateway 开启者 (platform, user_id)；CLI/TUI 为 None（本地单用户）
    owner: Optional[tuple[str, str]] = None
    gateway_session_key: Optional[str] = None
    sandbox: str = DEFAULT_SANDBOX_MODE
    # 本会话的 codex 线程与其当前宿主 agent（弱引用），跨 agent 重建复用
    codex_session: Any = None
    owner_agent: Any = None


@dataclass
class _Scope:
    surface: str  # "cli" | "gateway" | "tui"
    session_id: Optional[str]
    gateway_session_key: Optional[str] = None
    owner: Optional[tuple[str, str]] = None


_lock = threading.Lock()
_modes_by_sid: dict[str, CodexMode] = {}
# gateway 首条消息前会话还没建：先按 gateway session_key 挂起，首轮再绑定 session_id
_pending_by_gateway_key: dict[str, CodexMode] = {}

# pre_gateway_dispatch 捕获的 (runner 弱引用, source)。只在 /codex 消息上设置，
# 同一 _handle_message 协程里随后的插件命令分发读取。
_gateway_dispatch: contextvars.ContextVar = contextvars.ContextVar(
    "ext_codex_gateway_dispatch", default=None
)


# ------------------------------------------------------------------ 状态读写 ----
def get_mode(session_id: str) -> Optional[CodexMode]:
    with _lock:
        return _modes_by_sid.get(session_id)


def _store_mode(mode: CodexMode) -> None:
    with _lock:
        if mode.session_id:
            _modes_by_sid[mode.session_id] = mode
            # 已绑定到具体会话：清掉同 key 的挂起项，免得 /new 后被新会话误领
            if mode.gateway_session_key:
                _pending_by_gateway_key.pop(mode.gateway_session_key, None)
        elif mode.gateway_session_key:
            _pending_by_gateway_key[mode.gateway_session_key] = mode


def _lookup_mode(scope: _Scope) -> Optional[CodexMode]:
    with _lock:
        if scope.session_id and scope.session_id in _modes_by_sid:
            return _modes_by_sid[scope.session_id]
        if scope.gateway_session_key:
            return _pending_by_gateway_key.get(scope.gateway_session_key)
    return None


def _drop_mode(scope: _Scope) -> Optional[CodexMode]:
    with _lock:
        dropped = None
        if scope.session_id:
            dropped = _modes_by_sid.pop(scope.session_id, None)
        if scope.gateway_session_key:
            pending = _pending_by_gateway_key.pop(scope.gateway_session_key, None)
            dropped = dropped or pending
        return dropped


def _close_if_idle(session: Any) -> None:
    """关闭 codex 线程（空闲时立即关；正在跑的轮次由下一轮边界收尾）。"""
    if session is None or session.closed or session.turn_active:
        return
    try:
        session.close()
    except Exception:
        logger.debug("/codex: codex session close failed", exc_info=True)


def _release_codex_session(mode: Optional[CodexMode]) -> None:
    _close_if_idle(getattr(mode, "codex_session", None) if mode is not None else None)


def _mode_for_agent(agent: Any) -> Optional[CodexMode]:
    sid = str(getattr(agent, "session_id", "") or "")
    with _lock:
        mode = _modes_by_sid.get(sid)
        if mode is not None:
            return mode
        gw_key = getattr(agent, "_gateway_session_key", None)
        pending = _pending_by_gateway_key.get(gw_key) if gw_key else None
        if pending is not None and sid:
            del _pending_by_gateway_key[gw_key]
            pending.session_id = sid
            _modes_by_sid[sid] = pending
            return pending
    return None


# -------------------------------------------------------------- 会话识别 ----
def _current_identity():
    try:
        from tools.user_env_runtime import get_current_user_env_identity

        return get_current_user_env_identity()
    except Exception:
        return None


def _gateway_scope() -> Optional[_Scope]:
    captured = _gateway_dispatch.get()
    if not captured:
        return None
    runner_ref, source = captured
    runner = runner_ref()
    identity = _current_identity()
    if runner is None or source is None or identity is None:
        return None
    platform = source.platform.value if getattr(source, "platform", None) else ""
    # 防串：捕获的 source 必须就是 gateway 为本次插件命令绑定的调用者
    if (str(platform).strip(), str(source.user_id or "").strip()) != (
        identity.platform,
        identity.user_id,
    ):
        return None
    try:
        session_key = runner._session_key_for_source(source)
        store = getattr(runner, "session_store", None)
        session_id = store.peek_session_id(session_key) if store is not None else None
    except Exception:
        logger.warning("/codex: gateway session lookup failed", exc_info=True)
        return None
    return _Scope(
        "gateway",
        session_id,
        gateway_session_key=session_key,
        owner=(identity.platform, identity.user_id),
    )


def _cli_scope() -> Optional[_Scope]:
    try:
        from hermes_cli.plugins import get_plugin_manager

        cli = getattr(get_plugin_manager(), "_cli_ref", None)
    except Exception:
        return None
    if cli is None:
        return None
    agent = getattr(cli, "agent", None)
    sid = getattr(agent, "session_id", None) or getattr(cli, "session_id", None)
    return _Scope("cli", str(sid)) if sid else None


def _tui_scope() -> tuple[Optional[_Scope], str]:
    mod = sys.modules.get("tui_gateway.server")
    sessions = getattr(mod, "_sessions", None) if mod is not None else None
    if not isinstance(sessions, dict):
        return None, ""
    lock = getattr(mod, "_sessions_lock", None) or threading.RLock()
    with lock:
        records = [s for s in sessions.values() if isinstance(s, dict)]
    if len(records) != 1:
        # 插件命令拿不到 TUI sid，多会话时无法确定是哪个会话发起的
        return None, "TUI 进程内存在多个会话，无法确定当前会话"
    record = records[0]
    agent = record.get("agent")
    sid = getattr(agent, "session_id", None) or record.get("session_key")
    return (_Scope("tui", str(sid)) if sid else None), ""


def _resolve_scope() -> tuple[Optional[_Scope], str]:
    scope = _gateway_scope() or _cli_scope()
    if scope is not None:
        return scope, ""
    scope, reason = _tui_scope()
    if scope is not None:
        return scope, ""
    return None, reason or "无法识别当前会话"


# -------------------------------------------------------------- 权限 ----
def _rbac_role(platform: str, user_id: str) -> Optional[str]:
    """rbac-guard 已加载时返回调用者角色；未加载返回 None。查询失败抛出（由调用方拒绝）。"""
    for mod in list(sys.modules.values()):
        path = str(getattr(mod, "__file__", "") or "").replace("\\", "/")
        if path.endswith("plugins/rbac-guard/roles.py"):
            role_for = getattr(mod, "role_for", None)
            if callable(role_for):
                return role_for(platform, user_id)
    return None


def _gateway_enable_denied(scope: _Scope) -> Optional[str]:
    if scope.surface != "gateway" or scope.owner is None:
        return None
    try:
        role = _rbac_role(*scope.owner)
    except Exception as exc:
        logger.warning("/codex: rbac role lookup failed: %s", exc)
        return "❌ 无法确认你的角色，拒绝开启 Codex 模式。"
    if role is not None and role != "admin":
        return (
            "❌ 仅 admin 可开启 Codex 模式：codex 在自己的进程里执行命令，"
            "不受 Hermes RBAC 工具权限约束。"
        )
    return None


def _owner_matches(mode: CodexMode, platform: str, sender_id: str) -> bool:
    if mode.owner is None:
        return True
    owner_platform, owner_uid = mode.owner
    turn_platform = str(platform or "").strip()
    # A2A 等执行面会把 platform 标成 "{platform}_a2a"，身份平台仍是 "{platform}"
    same_platform = turn_platform == owner_platform or turn_platform.startswith(
        f"{owner_platform}_"
    )
    return same_platform and str(sender_id or "").strip() == owner_uid


# -------------------------------------------------------- agent 运行时切换 ----
def _publish_aux_runtime(agent: Any, api_mode: str) -> None:
    """按宿主 api_mode 重新发布本轮辅助调用的主运行时（turn_context 在切换前已发布过一次）。"""
    try:
        from agent.auxiliary_client import set_runtime_main

        set_runtime_main(
            getattr(agent, "provider", "") or "",
            getattr(agent, "model", "") or "",
            requested_provider=getattr(agent, "requested_provider", "") or "",
            base_url=getattr(agent, "base_url", "") or "",
            api_key=getattr(agent, "api_key", "") or "",
            api_mode=api_mode or "",
            auth_mode=getattr(agent, "auth_mode", "") or "",
            session_id=getattr(agent, "session_id", "") or "",
        )
    except Exception:
        logger.debug("/codex: aux runtime publish failed", exc_info=True)


def _pin_host_main_runtime(agent: Any) -> None:
    """让 agent._current_main_runtime() 报告宿主 api_mode。

    后台 review fork 与压缩可行性检查从这里取运行时；core 对 codex_app_server 的
    处理是降级成 codex_responses（原生 runtime 只服务 openai-codex），会把
    chat_completions / anthropic_messages 宿主 provider 路由到错误协议。
    """
    class_impl = getattr(type(agent), "_current_main_runtime", None)
    if not callable(class_impl):
        return
    agent_ref = weakref.ref(agent)

    def _current_main_runtime():
        target = agent_ref()
        if target is None:
            return {}
        runtime = class_impl(target)
        host = target.__dict__.get(_HOST_ATTR)
        if host and runtime.get("api_mode") == CODEX_API_MODE:
            runtime["api_mode"] = host["api_mode"]
        return runtime

    agent.__dict__["_current_main_runtime"] = _current_main_runtime


def _close_codex_session(agent: Any) -> None:
    session = getattr(agent, "_codex_session", None)
    if session is None:
        return
    agent._codex_session = None
    try:
        session.close()
    except Exception:
        logger.debug("/codex: codex session close failed", exc_info=True)


def _effective_cwd(agent: Any) -> str:
    # 与 run_codex_app_server_turn() 创建 CodexAppServerSession 时的取值一致
    from agent.runtime_cwd import resolve_agent_cwd

    return getattr(agent, "session_cwd", None) or str(resolve_agent_cwd())


def enter_codex(agent: Any, mode: CodexMode) -> bool:
    """把 agent 切到 codex_app_server。返回 False 表示已是全局 codex runtime，未接管。"""
    host = agent.__dict__.get(_HOST_ATTR)
    current = getattr(agent, "api_mode", "") or ""
    if host is None:
        if current == CODEX_API_MODE:
            return False
        host = {
            "api_mode": current,
            "session_cwd": agent.__dict__.get("session_cwd", _MISSING),
        }
        agent.__dict__[_HOST_ATTR] = host
        _pin_host_main_runtime(agent)
    elif current != CODEX_API_MODE:
        # 期间宿主运行时被 /model 等改过，以最新值为准
        host["api_mode"] = current

    if mode.cwd:
        agent.session_cwd = mode.cwd
    elif host["session_cwd"] is _MISSING:
        agent.__dict__.pop("session_cwd", None)
    else:
        agent.session_cwd = host["session_cwd"]
    _bind_codex_session(agent, mode)

    agent.api_mode = CODEX_API_MODE
    _publish_aux_runtime(agent, host["api_mode"])
    return True


def _bind_codex_session(agent: Any, mode: CodexMode) -> None:
    """保证本轮 agent 挂着会话的 codex 线程：沿用、从旧 agent 接管，或新建。"""
    from agent.codex_runtime import attach_codex_app_server_session

    cwd = _effective_cwd(agent)
    params = thread_params_for(mode.sandbox)

    def _reusable(session: Any) -> bool:
        return (
            session is not None
            and not session.closed
            and session.cwd == cwd
            and session.thread_start_params == params
        )

    current = getattr(agent, "_codex_session", None)
    if current is not None and not _reusable(current):
        _close_codex_session(agent)  # 已退役，或 cwd / 沙箱档位变了：重开 codex 线程
        current = None

    if current is None:
        candidate = mode.codex_session
        if _reusable(candidate):
            previous = mode.owner_agent() if mode.owner_agent is not None else None
            if (
                previous is not None
                and previous is not agent
                and getattr(previous, "_codex_session", None) is candidate
            ):
                # 旧 agent 放手，免得它被关闭/驱逐时顺带关掉这个线程
                previous._codex_session = None
            attach_codex_app_server_session(agent, candidate)
            logger.info("/codex: session %s reusing codex thread on rebuilt agent", mode.session_id)
        else:
            _release_codex_session(mode)
            attach_codex_app_server_session(agent, cwd=cwd, thread_start_params=params)

    mode.codex_session = agent._codex_session
    mode.owner_agent = weakref.ref(agent)


def leave_codex(agent: Any, *, close_session: bool) -> bool:
    """恢复宿主运行时。返回 False 表示该 agent 未被本插件切换过。"""
    host = agent.__dict__.pop(_HOST_ATTR, None)
    if host is None:
        return False
    agent.__dict__.pop("_current_main_runtime", None)
    if getattr(agent, "api_mode", None) == CODEX_API_MODE:
        agent.api_mode = host["api_mode"]
    if host["session_cwd"] is _MISSING:
        agent.__dict__.pop("session_cwd", None)
    else:
        agent.session_cwd = host["session_cwd"]
    if close_session:
        _close_codex_session(agent)
    _publish_aux_runtime(agent, getattr(agent, "api_mode", "") or "")
    return True


# ------------------------------------------------------------------ hooks ----
def on_pre_llm_call(session_id="", platform="", sender_id="", parent_session_id="", **_):
    """每轮开始（codex 分支判断之前、agent 线程上）按会话状态切换运行时。"""
    try:
        from agent.subagent_lifecycle import get_active_subagent_parent

        agent = get_active_subagent_parent()
    except Exception:
        return None
    sid = str(session_id or "")
    if agent is None or not sid or str(getattr(agent, "session_id", "") or "") != sid:
        return None
    # 后台 review fork 共享父会话 session_id，但必须留在 Hermes loop（要调 memory/skill_manage）
    if (
        getattr(agent, "_memory_write_origin", "") == "background_review"
        or (parent_session_id and parent_session_id == sid)
    ):
        return None

    mode = _mode_for_agent(agent)
    if mode is None:
        if leave_codex(agent, close_session=True):
            logger.info("/codex: session %s restored to Hermes loop", sid)
    elif not _owner_matches(mode, platform, sender_id):
        # 共享会话里非开启者的消息：本轮走 Hermes，保留 codex 线程给开启者
        leave_codex(agent, close_session=False)
    elif enter_codex(agent, mode):
        logger.info("/codex: session %s turn routed to codex app-server", sid)
    return None


def on_pre_gateway_dispatch(event=None, gateway=None, **_):
    """只为 /codex 消息捕获 (runner, source)，供随后的插件命令识别会话。不影响分发。"""
    if event is None or gateway is None:
        return None
    try:
        command = (event.get_command() or "").replace("_", "-")
    except Exception:
        return None
    if command == "codex":
        _gateway_dispatch.set((weakref.ref(gateway), event.source))
    return None


def on_session_finalize(session_id="", **_):
    if session_id:
        with _lock:
            mode = _modes_by_sid.pop(str(session_id), None)
        _release_codex_session(mode)


# ---------------------------------------------------------------- command ----
def _help_text() -> str:
    return (
        "**/codex** — 把当前会话切换到本地 Codex CLI（`codex app-server`）执行\n"
        "只作用于当前会话，不修改 config.yaml、~/.codex/config.toml 或 MCP 配置。\n"
        "\n"
        "**子命令**\n"
        "  /codex on [cwd=DIR] [sandbox=read|network|full] — 开启 Codex 模式：下一条消息起整轮对话交给本地 codex 执行，结果回传本会话\n"
        "  /codex off — 关闭 Codex 模式：下一条消息起恢复 Hermes 默认 loop，并关闭本会话的 codex 线程\n"
        "  /codex status — 查看当前会话的开关状态、cwd 与开启者\n"
        "  /codex help — 显示本帮助（不带任何参数时默认显示）\n"
        "\n"
        "**参数**\n"
        "  cwd=DIR — 仅 `on` 可用，codex 线程的工作目录：codex 在此目录下执行命令、读写文件\n"
        "    • 默认：会话工作目录——依次取会话自带的 cwd（ACP / 网关会话固定目录）"
        "→ `terminal.cwd`（TERMINAL_CWD）→ Hermes 启动目录\n"
        "    • 支持 `~` 与相对路径（相对 `terminal.cwd` 解析，未设置时相对启动目录）；目录必须已存在；"
        "路径含空格时加引号：`cwd=\"~/my proj\"`\n"
        "    • 再次 `on` 不带 cwd 时沿用上次指定的目录；更换目录会在下一轮重开 codex 线程\n"
        "  sandbox=read|network|full — 仅 `on` 可用，codex 线程的沙箱档位，默认 `network`\n"
        "    • `read`：只读沙箱，不能写文件，网络关闭；越出沙箱需审批\n"
        "    • `network`（默认）：工作区可写（cwd、/tmp、$TMPDIR），网络开启；越出沙箱需审批\n"
        "    • `full`：⚠️ 无沙箱，任意命令、任意文件、网络均不受限，且从不请求审批\n"
        "    • 每次 `on` 不带 sandbox 时恢复默认 `network`；切换档位会重开 codex 线程\n"
        "    • 审批：CLI 会弹出 Hermes 审批；飞书 / TUI 等无审批界面的入口，越出沙箱的请求会被自动拒绝\n"
        "\n"
        "**默认行为**\n"
        "  • 每个会话默认关闭，使用 Hermes 默认 loop\n"
        "  • 开启后同一会话多轮复用同一个 codex 线程；线程从空白上下文开始，看不到开启前的 Hermes 对话\n"
        "  • 模型、鉴权、sandbox、MCP 均使用本机 ~/.codex 现有配置；"
        "memory / delegate_task 等 Hermes 工具在该模式下不可用\n"
        "  • 沙箱与网络由 sandbox 档位决定（不再取决于目录 trust），`on` 与 `status` 会显示 codex 报告的实际沙箱、网络与审批\n"
        "  • 换会话（/new、自动重置）后自动失效\n"
        "  • 消息平台：启用 rbac-guard 时仅 admin 可开启；群聊中只有开启者本人的消息走 Codex"
    )


def _parse(raw_args: str) -> tuple[str, dict[str, str], list[str]]:
    tokens = shlex.split(raw_args or "")
    sub = tokens[0].lower() if tokens else "help"
    options: dict[str, str] = {}
    extras: list[str] = []
    for token in tokens[1:]:
        key, sep, value = token.partition("=")
        if sep and key:
            options[key.lower()] = value
        else:
            extras.append(token)
    return sub, options, extras


def _resolve_cwd(raw: str) -> tuple[Optional[str], str]:
    from agent.runtime_cwd import resolve_agent_cwd

    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = resolve_agent_cwd() / path
    try:
        path = path.resolve()
    except OSError as exc:
        return None, f"❌ cwd 无效：{exc}"
    if not path.is_dir():
        return None, f"❌ cwd 不是已存在的目录：`{path}`"
    return str(path), ""


def _codex_binary() -> tuple[bool, str]:
    try:
        from agent.transports.codex_app_server import check_codex_binary

        return check_codex_binary()
    except Exception as exc:
        return False, f"codex 检查失败：{exc}"


_SANDBOX_LABELS = {
    "readOnly": "只读：不能写任何文件",
    "workspaceWrite": "工作区可写",
    "dangerFullAccess": "不受限：可读写任意文件",
}
_APPROVAL_LABELS = {
    "on-request": "沙箱内命令直接执行，需越出沙箱时才请求审批",
    "on-failure": "沙箱内执行失败时请求越权重试",
    "untrusted": "除少量安全的只读命令外都要审批",
    "never": "从不请求审批，越出沙箱的操作直接失败",
}
_NO_SANDBOX = "dangerFullAccess"


def _approval_bypass_active(scope: _Scope) -> bool:
    try:
        from tools.approval import (
            is_approval_bypass_active,
            is_approval_bypass_active_for_session,
        )

        if scope.gateway_session_key:
            return is_approval_bypass_active_for_session(scope.gateway_session_key)
        return is_approval_bypass_active()
    except Exception:
        return False


def _policy_lines(policy: dict, scope: _Scope) -> list[str]:
    """把 codex 报告的实际策略翻译成用户可读的几行。"""
    sandbox = policy.get("sandbox")
    no_sandbox = sandbox == _NO_SANDBOX
    sandbox_text = "⚠️ 无沙箱：可读写任意文件" if no_sandbox else _SANDBOX_LABELS.get(sandbox, str(sandbox))
    if sandbox == "workspaceWrite":
        writable = ["cwd"] + [f"`{root}`" for root in policy.get("writable_roots") or []]
        if policy.get("slash_tmp_writable"):
            writable.append("/tmp")
        if policy.get("tmpdir_writable"):
            writable.append("$TMPDIR")
        sandbox_text += f"（可写：{'、'.join(writable)}）"
    if not no_sandbox:
        sandbox_text += "；读取不受限"
    if no_sandbox:
        network_text = "⚠️ 不受限（无沙箱）"
    elif policy.get("network_access"):
        network_text = "开启"
    else:
        network_text = "关闭"

    approval = policy.get("approval_policy")
    approval_key = approval if isinstance(approval, str) else None
    if approval_key == "never" and no_sandbox:
        approval_label = "从不请求审批，所有操作直接执行"
    else:
        approval_label = _APPROVAL_LABELS.get(approval_key, "见 codex 配置")
    approval_text = f"`{approval}` — {approval_label}"
    if approval_key != "never":
        if _approval_bypass_active(scope):
            approval_text += "；越权请求由 Hermes 自动批准（approvals.mode=off / yolo）"
        elif scope.surface == "cli":
            approval_text += "；越权请求会在 CLI 中弹出 Hermes 审批"
        else:
            approval_text += "；当前入口没有审批界面，越权请求会被自动拒绝"

    lines = []
    if policy.get("permission_profile"):  # 按线程指定 sandbox 时 codex 不报告权限档
        lines.append(f"  • 权限档：`{policy['permission_profile']}`")
    return lines + [
        f"  • 沙箱：{sandbox_text}",
        f"  • 网络：{network_text}",
        f"  • 审批：{approval_text}",
    ]


def _session_lines(mode: CodexMode, scope: _Scope) -> list[str]:
    session = mode.codex_session
    policy = session.effective_policy if session is not None and not session.closed else None
    if policy is None:
        return ["  • codex 线程：未启动（下一条消息时启动），权限以届时 codex 报告为准"]
    lines = [f"  • codex 线程 cwd：`{session.cwd}`"]
    if policy.get("sandbox") == _NO_SANDBOX:
        lines.append(f"  {_NO_SANDBOX_WARNING}")
    lines.append("  **实际生效权限（codex 报告）**")
    return lines + _policy_lines(policy, scope)


def _describe(scope: _Scope, mode: Optional[CodexMode]) -> str:
    session_label = scope.session_id or "（首条消息后创建）"
    if mode is None:
        return f"💤 当前会话 `{session_label}` 使用 Hermes 默认 loop。发送 /codex on 切到 Codex。"
    lines = [
        f"🟢 当前会话 `{session_label}` 已开启 Codex 模式（codex app-server）",
        f"  • cwd：`{mode.cwd}`" if mode.cwd else "  • cwd：会话默认工作目录",
        f"  • 沙箱档位：`{mode.sandbox}`（{_SANDBOX_MODE_LABELS[mode.sandbox]}）",
    ]
    if mode.owner is not None:
        lines.append(f"  • 开启者：`{mode.owner[0]}:{mode.owner[1]}`（共享会话中仅其消息走 Codex）")
    return "\n".join(lines + _session_lines(mode, scope))


@dataclass
class _EnablePlan:
    """/codex on 的收尾：启动（或沿用）codex 线程拿到实际权限后再生效。"""

    scope: _Scope
    mode: CodexMode
    session: Any
    started_here: bool
    version: str
    replaced: Any = None

    def start(self) -> Optional[str]:
        if self.session.effective_policy is not None:
            return None
        try:
            self.session.ensure_started()
        except Exception as exc:
            return str(exc) or type(exc).__name__
        return None

    def complete(self, error: Optional[str]) -> str:
        if error:
            if self.started_here:
                try:
                    self.session.close()
                except Exception:
                    logger.debug("/codex: codex session close failed", exc_info=True)
            return f"❌ 无法开启 Codex 模式：codex app-server 启动失败：{error}"
        self.mode.codex_session = self.session
        _store_mode(self.mode)
        if self.replaced is not None:
            _close_if_idle(self.replaced)  # cwd / 沙箱档位变了：旧线程作废

        policy = self.session.effective_policy or {}
        lines = [f"✅ 已开启 Codex 模式（codex CLI {self.version}）"]
        if policy.get("sandbox") == _NO_SANDBOX:
            lines.append(_NO_SANDBOX_WARNING)
        lines += [
            "下一条消息起整轮由本地 `codex app-server` 执行，结果桥接回本会话；"
            "模型、鉴权、MCP 使用本机 ~/.codex 现有配置。",
            f"  • cwd：`{self.session.cwd}`",
            f"  • 沙箱档位：`{self.mode.sandbox}`（{_SANDBOX_MODE_LABELS[self.mode.sandbox]}）",
            "  • Codex 线程从空白上下文开始（看不到此前的 Hermes 对话），"
            "之后本会话内多轮复用同一个线程；memory / delegate_task 等 Hermes 工具在该模式下不可用。",
        ]
        if self.scope.owner is not None:
            lines.append("  • 共享会话（群聊）中只有你的消息走 Codex，其他成员仍由 Hermes 处理。")
        if self.replaced is not None:
            lines.append(
                "  • 已切换 cwd 或沙箱档位：已重开 codex 线程，之前的 codex 上下文不再保留"
                + ("" if self.replaced.closed else "（旧线程在当前轮结束后关闭）")
            )
        lines.append("**实际生效权限（codex 报告）**")
        lines += _policy_lines(policy, self.scope)
        lines.append("发送 /codex off 恢复 Hermes 默认 loop。")
        return "\n".join(lines)


def _plan_enable(scope: _Scope, options: dict[str, str]):
    denied = _gateway_enable_denied(scope)
    if denied:
        return denied
    ok, version = _codex_binary()
    if not ok:
        return f"❌ 无法开启 Codex 模式：{version}"

    cwd = None
    if "cwd" in options:
        cwd, err = _resolve_cwd(options["cwd"])
        if err:
            return err
    # 不沿用：每次 on 未指定 sandbox 都回到默认档位
    sandbox_mode = (options.get("sandbox") or DEFAULT_SANDBOX_MODE).strip().lower()
    if sandbox_mode not in SANDBOX_MODES:
        return f"❌ 不支持的 sandbox：`{sandbox_mode}`，可选 read / network / full（默认 network）"
    existing = _lookup_mode(scope)
    if existing is not None and existing.owner not in (None, scope.owner):
        return "❌ 当前会话的 Codex 模式由其他成员开启，请先由其 /codex off。"
    mode = CodexMode(
        session_id=scope.session_id,
        cwd=cwd or (existing.cwd if existing else None),
        owner=scope.owner,
        gateway_session_key=scope.gateway_session_key,
        sandbox=sandbox_mode,
        codex_session=existing.codex_session if existing else None,
        owner_agent=existing.owner_agent if existing else None,
    )

    from agent.runtime_cwd import resolve_agent_cwd
    from agent.transports.codex_app_server_session import CodexAppServerSession

    thread_cwd = mode.cwd or str(resolve_agent_cwd())
    params = thread_params_for(sandbox_mode)
    current = mode.codex_session
    if (
        current is not None
        and not current.closed
        and current.cwd == thread_cwd
        and current.thread_start_params == params
    ):
        return _EnablePlan(scope, mode, current, started_here=False, version=version)
    # 回调在第一轮由 _bind_codex_session 绑定到 agent 线程上
    session = CodexAppServerSession(cwd=thread_cwd, thread_start_params=params)
    replaced = current if current is not None and not current.closed else None
    return _EnablePlan(
        scope, mode, session, started_here=True, version=version, replaced=replaced,
    )


def codex_command(raw_args: str = ""):
    try:
        sub, options, extras = _parse(raw_args)
    except ValueError as exc:
        return f"❌ 参数解析失败：{exc}\n\n{_help_text()}"

    if sub in ("help", "-h", "--help"):
        return _help_text()
    if sub not in ("on", "off", "status"):
        return f"未知子命令：`{sub}`\n\n{_help_text()}"
    unknown = extras + [k for k in options if not (sub == "on" and k in ("cwd", "sandbox"))]
    if unknown:
        return f"❌ 不支持的参数：{', '.join(unknown)}\n\n{_help_text()}"

    scope, reason = _resolve_scope()
    if scope is None:
        return f"❌ {reason}，/codex 仅作用于当前会话。"

    if sub == "status":
        return _describe(scope, _lookup_mode(scope))

    if sub == "off":
        mode = _drop_mode(scope)
        if mode is None:
            return "当前会话未开启 Codex 模式。"
        _release_codex_session(mode)
        return "✅ 已关闭 Codex 模式，下一条消息起恢复 Hermes 默认 loop。"

    plan = _plan_enable(scope, options)
    if not isinstance(plan, _EnablePlan):
        return plan
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return plan.complete(plan.start())

    async def _enable_off_loop() -> str:
        # gateway 在事件循环上直接调用插件命令：codex 启动放到线程里，免得卡住其他会话
        return plan.complete(await asyncio.to_thread(plan.start))

    return _enable_off_loop()


def register(ctx):
    ctx.register_command(
        name="codex",
        handler=codex_command,
        description=(
            "当前会话切换到本地 Codex CLI（codex app-server）执行整轮对话，"
            "会话级开关，不修改任何配置"
        ),
        args_hint="on [cwd=DIR] [sandbox=read|network|full] | off | status | help",
    )
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("pre_gateway_dispatch", on_pre_gateway_dispatch)
    ctx.register_hook("on_session_finalize", on_session_finalize)
