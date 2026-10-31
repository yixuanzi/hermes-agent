"""codex_cmd — /codex slash 命令：把当前会话切到本地 codex app-server 执行。

/codex on [cwd=DIR] [sandbox=read|network|full]
                      当前会话下一条消息起，整轮交给本地 codex CLI（codex app-server）
/codex off            恢复 Hermes 默认 loop
/codex status         查看当前会话状态
/codex new            在 Codex 模式内开一个全新的 codex 线程（cwd / 沙箱 / 模型选择保持）
/codex stop           中断 codex 正在执行的这一轮，模式与线程保留
/codex compact        让 codex 压缩自己的线程上下文（/compress 由 Hermes 核心处理，效果相同）
/codex model [set model=ID effort=LEVEL]
                      列出 / 设置本会话的模型与思考深度（effort）
/codex [help]         命令帮助（不带参数时默认显示）

设计要点：
  - 会话级：开关状态按 Hermes session_id 记在本插件内存里，不写 config.yaml，
    不改 ~/.codex/config.toml，不迁移 MCP。codex 子进程直接使用本机已配置好的
    codex CLI（模型、鉴权、sandbox、MCP 均取自 ~/.codex）。/new 等换会话后自动失效。
  - 上下文压缩：核心 /compress 在该模式下不经过插件，直接转成 codex 原生的 thread
    compaction（compact_thread）；/codex compact 是同一个动作的显式入口，不替代 /compress。
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
  - 多后端：会话表、轮次切换与线程归属是通用的；CodexMode.backend 决定挂到
    agent._codex_session 上的是什么（None = codex app-server；/claude 见 claude_cmd）。
    一个会话同一时刻只有一种模式，开启一个即替换另一个。
  - 原文直通：网关给入站消息加的 <source>、群聊历史、引用等不发给后端，只发用户原文
    （附件消息保留附件说明），见 raw_prompt。
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

import raw_prompt  # noqa: E402

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
_PREF_KEYS = ("model", "effort")  # /codex model set 可设；作为 turn/start 的同名字段发出
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


def _wrap_client(client: Any, session: Any) -> Any:
    """给 codex 客户端的 request 加两处旁路（core 不改）：
    turn/start 带上 /codex model set 选定的 model / effort（对该轮及后续轮生效，线程上下文不丢）；
    thread/start 的响应里记下 codex 实际用的 model / reasoningEffort（core 只取了权限部分）。"""
    if getattr(client, "_ext_prefs_wrapped", False):
        return client
    original = client.request

    def request(method, params=None, timeout=30.0):
        if method == "turn/start":
            prefs = getattr(session, "_ext_prefs", None)
            if prefs:
                params = {**(params or {}), **prefs}
        result = original(method, params, timeout=timeout)
        if method == "thread/start" and isinstance(result, dict):
            session._ext_thread_info = {
                "model": result.get("model"), "effort": result.get("reasoningEffort"),
            }
        return result

    client.request = request
    client._ext_prefs_wrapped = True
    return client


def install_prefs(session: Any, prefs: Optional[dict] = None) -> Any:
    """让 CodexAppServerSession 支持会话级 model / effort 偏好（幂等）。"""
    if getattr(session, "_ext_prefs", None) is None:
        session._ext_prefs = {k: v for k, v in (prefs or {}).items() if k in _PREF_KEYS and v}
        factory = session._client_factory
        session._client_factory = lambda **kwargs: _wrap_client(factory(**kwargs), session)
    if getattr(session, "_client", None) is not None:
        _wrap_client(session._client, session)
    return session


def _prefs_of(session: Any) -> dict:
    return dict(getattr(session, "_ext_prefs", None) or {})


class _CodexBackend:
    """会话表的默认后端：codex app-server。其他后端（claude_cmd.ClaudeBackend）提供同名成员。"""

    name = "codex"
    title = "Codex"

    @staticmethod
    def reusable(session: Any, cwd: str, mode: "CodexMode") -> bool:
        from agent.transports.codex_app_server_session import CodexAppServerSession

        return (
            isinstance(session, CodexAppServerSession)
            and session.cwd == cwd
            and session.thread_start_params == thread_params_for(mode.sandbox)
        )

    @staticmethod
    def attach(agent: Any, mode: "CodexMode", *, session: Any = None, cwd: str = "", previous: Any = None):
        from agent.codex_runtime import attach_codex_app_server_session

        if session is not None:
            return install_prefs(attach_codex_app_server_session(agent, session))
        # 线程被运行时退役后重建：沿用 /codex model set 的选择
        return install_prefs(
            attach_codex_app_server_session(
                agent, cwd=cwd, thread_start_params=thread_params_for(mode.sandbox)
            ),
            _prefs_of(previous),
        )


CODEX_BACKEND = _CodexBackend()
# 由本插件的 pre_gateway_dispatch 捕获调用者的会话命令
SESSION_COMMANDS = frozenset({"codex", "claude"})
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
    # None = codex app-server；/claude 为 claude_cmd.ClaudeBackend
    backend: Any = None


def backend_of(mode: CodexMode) -> Any:
    return mode.backend if mode.backend is not None else CODEX_BACKEND


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


def _gateway_enable_denied(scope: _Scope, backend: Any = CODEX_BACKEND) -> Optional[str]:
    if scope.surface != "gateway" or scope.owner is None:
        return None
    try:
        role = _rbac_role(*scope.owner)
    except Exception as exc:
        logger.warning("/%s: rbac role lookup failed: %s", backend.name, exc)
        return f"❌ 无法确认你的角色，拒绝开启 {backend.title} 模式。"
    if role is not None and role != "admin":
        return (
            f"❌ 仅 admin 可开启 {backend.title} 模式：{backend.name} 在自己的进程里执行命令，"
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
    """保证本轮 agent 挂着会话后端的线程：沿用、从旧 agent 接管，或新建。"""
    backend = backend_of(mode)
    cwd = _effective_cwd(agent)

    def _reusable(session: Any) -> bool:
        return session is not None and not session.closed and backend.reusable(session, cwd, mode)

    current = getattr(agent, "_codex_session", None)
    if current is not None and not _reusable(current):
        _close_codex_session(agent)  # 已退役，或 cwd / 沙箱档位 / 后端变了：重开线程
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
            backend.attach(agent, mode, session=candidate)
            logger.info("/%s: session %s reusing thread on rebuilt agent", backend.name, mode.session_id)
        else:
            _release_codex_session(mode)
            backend.attach(agent, mode, cwd=cwd, previous=candidate)

    raw_prompt.install_session_passthrough(agent._codex_session)
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
        logger.info("/%s: session %s turn routed to %s", backend_of(mode).name, sid, backend_of(mode).title)
    return None


def on_pre_gateway_dispatch(event=None, gateway=None, **_):
    """只为 /codex、/claude 消息捕获 (runner, source)，供随后的插件命令识别会话。不影响分发。

    也在这里给 runner 装上入站消息原文的记录（见 raw_prompt）。"""
    if event is None or gateway is None:
        return None
    raw_prompt.install_gateway_capture(gateway)
    try:
        command = (event.get_command() or "").replace("_", "-")
    except Exception:
        return None
    if command in SESSION_COMMANDS:
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
        "  /codex new — 在 Codex 模式内开一个全新的 codex 线程（清空 codex 上下文），cwd / 沙箱 / 模型选择保持；"
        "回合在执行时不可用（先 /stop）\n"
        "  /codex stop — 中断 Codex 正在执行的这一轮（与核心 /stop 效果相同），模式与线程保留\n"
        "  /codex compact — 让 codex 压缩自己的线程上下文（codex 原生 compaction）；"
        "与 Hermes 的 /compress 效果相同（后者由核心处理）；回合在执行时不可用\n"
        "  /codex model — 列出 codex 可用的模型与当前模型支持的思考深度（effort），标出当前值\n"
        "  /codex model set [model=ID] [effort=LEVEL] — 设置本会话的模型 / 思考深度（也可 `/codex model set ID`）；"
        "先设 model 再设 effort（可选深度随模型而变）；下一条消息起生效，线程上下文保留；"
        "仅 Codex 模式已开启时可用，只作用于本会话，不改 `~/.codex` 配置\n"
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
        "  • 换会话（/new、自动重置）后自动失效；/codex new 则只换 codex 线程、不退出 Codex 模式\n"
        "  • 与 /claude 互斥：开启 Codex 模式会关闭本会话的 Claude Code 模式，反之亦然\n"
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


def other_backend_note(mode: Optional[CodexMode], own: Any) -> Optional[str]:
    """会话当前处于另一个后端的模式时，给出提示（/codex 与 /claude 互相引用）。"""
    if mode is None or backend_of(mode) is own or backend_of(mode).name == own.name:
        return None
    other = backend_of(mode)
    return f"当前会话已开启 {other.title} 模式（/{other.name} status 查看，/{other.name} off 关闭）。"


def _describe(scope: _Scope, mode: Optional[CodexMode]) -> str:
    session_label = scope.session_id or "（首条消息后创建）"
    note = other_backend_note(mode, CODEX_BACKEND)
    if note:
        return f"💤 当前会话 `{session_label}` 未开启 Codex 模式。{note}"
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
    switched_from: Optional[str] = None  # 被替换的其他后端模式（如 Claude Code）

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
        if self.switched_from:
            lines.append(f"  • 已关闭本会话的 {self.switched_from} 模式")
        elif self.replaced is not None:
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
        other = backend_of(existing)
        return f"❌ 当前会话的 {other.title} 模式由其他成员开启，请先由其 /{other.name} off。"
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
    if current is not None and not current.closed and CODEX_BACKEND.reusable(current, thread_cwd, mode):
        return _EnablePlan(scope, mode, current, started_here=False, version=version)
    # 回调在第一轮由 _bind_codex_session 绑定到 agent 线程上
    session = install_prefs(
        CodexAppServerSession(cwd=thread_cwd, thread_start_params=params), _prefs_of(current)
    )
    replaced = current if current is not None and not current.closed else None
    switched_from = None
    if existing is not None and backend_of(existing) is not CODEX_BACKEND:
        switched_from = backend_of(existing).title
    return _EnablePlan(
        scope, mode, session, started_here=True, version=version, replaced=replaced,
        switched_from=switched_from,
    )


def _lookup_codex_mode(scope: _Scope) -> tuple[Optional[CodexMode], Optional[str]]:
    """model / new / stop 的共同前置：当前会话必须处于 Codex 模式。返回 (mode, 拒绝文案)。"""
    mode = _lookup_mode(scope)
    note = other_backend_note(mode, CODEX_BACKEND)
    if note or mode is None:
        return None, f"当前会话未开启 Codex 模式。{note or '发送 /codex on 先开启。'}"
    return mode, None


def _owner_only(mode: CodexMode, scope: _Scope, action: str) -> Optional[str]:
    if mode.owner not in (None, scope.owner):
        return f"❌ 当前会话的 Codex 模式由其他成员开启，只有开启者可以{action}。"
    return None


def _off_loop(work):
    """gateway 在事件循环上直接调用插件命令：会阻塞的 codex 请求放到线程里。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return work()

    async def _run() -> str:
        return await asyncio.to_thread(work)

    return _run()


def _list_models(session: Any) -> list[dict]:
    session.ensure_started()
    models: list[dict] = []
    cursor = None
    for _ in range(10):  # 分页上限，防止服务端游标异常时死循环
        params: dict[str, Any] = {"includeHidden": False, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        result = session._client.request("model/list", params, timeout=15)
        models += [m for m in result.get("data") or [] if isinstance(m, dict)]
        cursor = result.get("nextCursor")
        if not cursor:
            break
    return models


def _model_key(entry: dict) -> str:
    return str(entry.get("model") or entry.get("id") or "")


def _current_choice(session: Any, key: str, models: list[dict]) -> Optional[str]:
    """已选 > thread/start 上报 > catalog 默认。"""
    value = _prefs_of(session).get(key) or (getattr(session, "_ext_thread_info", None) or {}).get(key)
    if value or key != "model":
        return value
    return next((_model_key(m) for m in models if m.get("isDefault")), None)


def _efforts_of(models: list[dict], model: Optional[str]) -> list[dict]:
    for entry in models:
        if _model_key(entry) == model:
            return [e for e in entry.get("supportedReasoningEfforts") or [] if isinstance(e, dict)]
    return []


def _describe_models(session: Any, models: list[dict]) -> str:
    model = _current_choice(session, "model", models)
    effort = _current_choice(session, "effort", models)
    lines = ["**模型**（▶ 为当前）"]
    for entry in models:
        key = _model_key(entry)
        name = entry.get("displayName")
        extra = f"（{name}）" if name and name != key else ""
        desc = entry.get("description")
        lines.append(
            f"  {'▶' if key == model else ' '} `{key}`{extra}" + (f" — {desc}" if desc else "")
        )
    if not models:
        lines.append("  codex 未报告可选模型")
    efforts = _efforts_of(models, model)
    if efforts:
        lines.append(f"**思考深度 effort**（模型 `{model}`；▶ 为当前）")
        for entry in efforts:
            value = entry.get("reasoningEffort")
            desc = entry.get("description")
            lines.append(
                f"  {'▶' if value == effort else ' '} `{value}`" + (f" — {desc}" if desc else "")
            )
    else:
        lines.append("**思考深度 effort**：当前模型不支持或 codex 未报告")
    lines.append("设置：`/codex model set model=<ID> effort=<LEVEL>`")
    return "\n".join(lines)


def _model_command(scope: _Scope, extras: list[str], options: dict[str, str]):
    mode, denied = _lookup_codex_mode(scope)
    if denied:
        return denied
    session = mode.codex_session
    if session is None or session.closed or not hasattr(session, "_ext_prefs"):
        return "Codex 线程尚未就绪（下一条消息时启动）。"

    setting = bool(extras) and extras[0].lower() == "set"
    wanted: dict[str, str] = {}
    if setting:
        positional = extras[1:]
        if len(positional) > 1 or (positional and "model" in options):
            return f"❌ 参数有误。\n\n{_help_text()}"
        wanted = {k: v.strip() for k, v in options.items() if k in _PREF_KEYS and v.strip()}
        if positional:
            wanted["model"] = positional[0]
        if [k for k in options if k not in _PREF_KEYS] or not wanted:
            return "❌ 用法：`/codex model set [model=ID] [effort=LEVEL]`（也可 `/codex model set ID`）"
        denied = _owner_only(mode, scope, "修改")
        if denied:
            return denied
    elif extras or options:
        return f"❌ 不支持的参数：{', '.join(extras + list(options))}。设置请用 `/codex model set ...`"

    def work() -> str:
        try:
            models = _list_models(session)
        except Exception as exc:
            return f"❌ 获取 codex 模型列表失败：{exc}"
        if not setting:
            return _describe_models(session, models)
        prefs = _prefs_of(session)
        target = wanted.get("model") or _current_choice(session, "model", models)
        if "model" in wanted and wanted["model"] not in {_model_key(m) for m in models}:
            return f"❌ 未知模型：`{wanted['model']}`\n\n" + _describe_models(session, models)
        supported = [e.get("reasoningEffort") for e in _efforts_of(models, target)]
        if "effort" in wanted and wanted["effort"] not in supported:
            return f"❌ 模型 `{target}` 不支持思考深度 `{wanted['effort']}`\n\n" + _describe_models(session, models)
        if "model" in wanted:
            prefs["model"] = wanted["model"]
            if prefs.get("effort") not in supported:  # 换模型后原来的深度可能不再适用
                prefs.pop("effort", None)
        if "effort" in wanted:
            prefs["effort"] = wanted["effort"]
        session._ext_prefs = prefs
        done = [f"{k} → `{wanted[k]}`" for k in _PREF_KEYS if k in wanted]
        return "✅ 已设置：" + "、".join(done) + "（下一条消息起生效）\n\n" + _describe_models(session, models)

    return _off_loop(work)


def _new_command(scope: _Scope):
    mode, denied = _lookup_codex_mode(scope)
    if denied:
        return denied
    denied = _owner_only(mode, scope, "重置")
    if denied:
        return denied
    old = mode.codex_session
    if old is not None and not old.closed and old.turn_active:
        return "Codex 正在执行，请先 /stop 再 /codex new"

    from agent.runtime_cwd import resolve_agent_cwd
    from agent.transports.codex_app_server_session import CodexAppServerSession

    live = old is not None and not old.closed
    cwd = old.cwd if live else (mode.cwd or str(resolve_agent_cwd()))
    session = install_prefs(
        CodexAppServerSession(cwd=cwd, thread_start_params=thread_params_for(mode.sandbox)),
        _prefs_of(old),
    )

    def work() -> str:
        try:
            session.ensure_started()
        except Exception as exc:
            try:
                session.close()
            except Exception:
                logger.debug("/codex: codex session close failed", exc_info=True)
            return f"❌ 无法开启新的 codex 线程（原线程保持不变）：{str(exc) or type(exc).__name__}"
        mode.codex_session = session  # 下一轮边界 _bind_codex_session 把它绑到 agent 上
        _close_if_idle(old)
        return (
            "✅ 已开启新的 codex 线程：codex 上下文已清空，cwd / 沙箱 / 模型选择不变。"
            if live else "✅ 已准备好新的 codex 线程。"
        ) + "\n" + "\n".join(_session_lines(mode, scope))

    return _off_loop(work)


def _stop_command(scope: _Scope):
    mode, denied = _lookup_codex_mode(scope)
    if denied:
        return denied
    denied = _owner_only(mode, scope, "中断")
    if denied:
        return denied
    session = mode.codex_session
    if session is None or session.closed or not session.turn_active:
        return "Codex 当前没有正在执行的轮次。"
    session.request_interrupt()
    return "⏹ 已请求中断 Codex 当前这一轮，Codex 模式与线程保留。"


def _compact_command(scope: _Scope):
    mode, denied = _lookup_codex_mode(scope)
    if denied:
        return denied
    denied = _owner_only(mode, scope, "压缩")
    if denied:
        return denied
    session = mode.codex_session
    if session is None or session.closed or getattr(session, "_thread_id", None) is None:
        return "Codex 线程尚未启动（还没有可压缩的上下文）。"
    if session.turn_active:
        return "Codex 正在执行，请等本轮结束（或 /stop）后再压缩"

    def work() -> str:
        result = session.compact_thread()
        if result.should_retire:
            # 与核心 /compress 一致：线程可能已损坏，退役后下一轮重开
            _close_if_idle(session)
        if result.interrupted:
            return "⚠️ 压缩被中断。"
        if result.error:
            return f"❌ 压缩失败：{result.error}"
        text = "✅ 已让 Codex 压缩线程上下文。"
        used = (result.token_usage_last or {}).get("totalTokens")
        window = result.model_context_window
        if isinstance(used, int) and isinstance(window, int) and window > 0:
            text += f"上下文：{used:,} / {window:,} tokens（{used * 100 // window}%）"
        return text

    return _off_loop(work)


def codex_command(raw_args: str = ""):
    try:
        sub, options, extras = _parse(raw_args)
    except ValueError as exc:
        return f"❌ 参数解析失败：{exc}\n\n{_help_text()}"

    if sub in ("help", "-h", "--help"):
        return _help_text()
    if sub not in ("on", "off", "status", "new", "stop", "model", "compact"):
        return f"未知子命令：`{sub}`\n\n{_help_text()}"
    unknown = [] if sub == "model" else extras + [k for k in options if not (sub == "on" and k in ("cwd", "sandbox"))]
    if unknown:
        return f"❌ 不支持的参数：{', '.join(unknown)}\n\n{_help_text()}"

    scope, reason = _resolve_scope()
    if scope is None:
        return f"❌ {reason}，/codex 仅作用于当前会话。"

    if sub == "status":
        return _describe(scope, _lookup_mode(scope))
    if sub == "compact":
        return _compact_command(scope)
    if sub == "new":
        return _new_command(scope)
    if sub == "stop":
        return _stop_command(scope)
    if sub == "model":
        return _model_command(scope, extras, options)

    if sub == "off":
        note = other_backend_note(_lookup_mode(scope), CODEX_BACKEND)
        if note:
            return f"当前会话未开启 Codex 模式。{note}"
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
        args_hint="on [cwd=DIR] [sandbox=read|network|full] | off | status | new | stop | compact | model [set model=ID effort=LEVEL] | help",
    )
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("pre_gateway_dispatch", on_pre_gateway_dispatch)
    ctx.register_hook("on_session_finalize", on_session_finalize)
