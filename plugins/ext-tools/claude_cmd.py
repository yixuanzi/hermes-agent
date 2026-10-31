"""claude_cmd — /claude slash 命令：把当前会话切到本机 Claude Code 执行（经 ACP）。

/claude on [cwd=DIR] [mode=default|acceptEdits|plan|auto|bypass]
                      当前会话下一条消息起，整轮交给本机 Claude Code
                      （@agentclientprotocol/claude-agent-acp 驱动已安装、已登录的 claude CLI）
/claude off           恢复 Hermes 默认 loop
/claude status        查看当前会话状态
/claude [help]        命令帮助（不带参数时默认显示）

设计要点：
  - 与 /codex 同一套会话级机制（codex_cmd）：同一张会话表、同一个 pre_llm_call 轮次边界
    切换、同样的会话识别 / rbac admin / 群聊仅开启者 / 线程归会话所有并跨 AIAgent 重建
    复用。区别只在后端：CodexMode.backend = ClaudeBackend，挂到 agent._codex_session 上的是
    claude_acp.ClaudeAcpSession（与 CodexAppServerSession 同形），core 的 codex_app_server
    整轮接管路径（投影入库、token 统计、中断、steer、/compress）原样复用，core 不改。
    /codex 与 /claude 在一个会话里互斥，开启一个即替换另一个。
  - 本机配置：模型、鉴权（Claude Code 自己的登录）、MCP、hooks、CLAUDE.md、权限规则均取自
    ~/.claude；子进程不继承 Hermes 的 provider 密钥；不改任何配置文件。
  - 权限：mode= 为 Claude Code 的会话权限模式（session/set_mode），不指定则默认 auto。Claude Code 请求授权时：CLI 弹出 Hermes 审批；无审批界面的
    入口（飞书 / TUI 等）自动拒绝；approvals.mode=off / yolo 时自动批准。
  - 上下文连续：Claude Code 会话被 runtime 退役（进程崩溃、卡死）后，下一轮在同一 cwd 下
    用 session/resume 接上它；换 cwd 才从空白上下文开始。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, ClassVar, Optional

import codex_cmd
from claude_acp import (
    ACP_COMMAND_ENV,
    BYPASS_MODE,
    DEFAULT_ACP_PACKAGE,
    ClaudeAcpSession,
    ClaudeEventBridge,
    check_claude_code,
)

DEFAULT_MODE = "auto"  # /claude on 未指定 mode= 时的权限模式
_MODE_ALIASES = {
    "default": "default",
    "manual": "default",
    "acceptedits": "acceptEdits",
    "edits": "acceptEdits",
    "plan": "plan",
    "auto": "auto",
    "bypass": BYPASS_MODE,
    "bypasspermissions": BYPASS_MODE,
}
_MODE_LABELS = {
    "default": "编辑文件、执行未预授权的命令前都需要授权",
    "acceptEdits": "自动接受文件编辑；执行未预授权的命令仍需授权",
    "plan": "只规划不改动：不编辑文件、不执行有副作用的命令",
    "auto": "由 Claude 自行判断哪些操作需要授权",
    "dontAsk": "未预授权的操作一律拒绝",
    BYPASS_MODE: "⚠️ 跳过所有权限检查：任意命令与文件编辑直接执行",
}
_BYPASS_WARNING = (
    "⚠️ **bypassPermissions**：Claude Code 以当前系统账号直接执行任意命令、读写任意文件，"
    "不请求任何授权（Hermes 审批与 rbac-guard 均不生效）。"
)


def _session_hooks(agent: Any) -> dict:
    """在 agent 线程上取显示桥、审批回调（CLI 按线程安装）与审批豁免状态，与 codex 运行时一致。"""
    try:
        from tools.terminal_tool import _get_approval_callback

        approval_callback = _get_approval_callback()
    except Exception:
        approval_callback = None
    try:
        from tools.approval import is_approval_bypass_active

        auto_approve = bool(is_approval_bypass_active())
    except Exception:
        auto_approve = False
    return {
        "on_event": ClaudeEventBridge(agent),
        "approval_callback": approval_callback,
        "auto_approve": auto_approve,
    }


@dataclass
class ClaudeBackend:
    """codex_cmd 会话表里的 /claude 后端：Claude Code 会话的复用与创建。"""

    permission: Optional[str] = DEFAULT_MODE  # None = 沿用 Claude Code 配置的 permissions.defaultMode

    name: ClassVar[str] = "claude"
    title: ClassVar[str] = "Claude Code"

    def reusable(self, session: Any, cwd: str, mode: Any) -> bool:
        # 权限模式不必重开会话：下一回合开始前 session/set_mode 即可
        return isinstance(session, ClaudeAcpSession) and session.cwd == cwd

    def attach(self, agent: Any, mode: Any, *, session: Any = None, cwd: str = "", previous: Any = None):
        hooks = _session_hooks(agent)
        if session is None:
            # 同 cwd 下被退役的会话：session/resume 接上它的 Claude Code 上下文
            resume = (
                previous.acp_session_id
                if isinstance(previous, ClaudeAcpSession) and previous.cwd == cwd
                else None
            )
            session = ClaudeAcpSession(
                cwd=cwd, permission_mode=self.permission, resume_session_id=resume, **hooks
            )
        else:
            session.rebind(**hooks)
            session.set_permission_mode(self.permission)
        agent._codex_session = session
        return session


def _claude_code() -> tuple[bool, str]:
    return check_claude_code()


# ---------------------------------------------------------------- command ----
# 回复里的路径一律放进行内代码：网关会把正文中真实存在的 ~/….json 等路径当附件发送
# （extract_local_files，飞书上会多收到一个 settings.json），行内代码里的路径不算。
def _help_text() -> str:
    return (
        "**/claude** — 把当前会话切换到本机 Claude Code 执行（经 `@agentclientprotocol/claude-agent-acp`）\n"
        "只作用于当前会话，不修改 config.yaml 或 `~/.claude` 下的任何配置。\n"
        "\n"
        "**子命令**\n"
        "  /claude on [cwd=DIR] [mode=default|acceptEdits|plan|auto|bypass] — 开启 Claude Code 模式："
        "下一条消息起整轮对话交给本机 Claude Code 执行，结果回传本会话\n"
        "  /claude off — 关闭 Claude Code 模式：下一条消息起恢复 Hermes 默认 loop，并结束本会话的 Claude Code 会话\n"
        "  /claude status — 查看当前会话的开关状态、cwd、权限模式、模型与开启者\n"
        "  /claude help — 显示本帮助（不带任何参数时默认显示）\n"
        "\n"
        "**参数**\n"
        "  cwd=DIR — 仅 `on` 可用，Claude Code 的工作目录（CLAUDE.md、.claude/ 项目配置按此目录加载）\n"
        "    • 默认：会话工作目录——依次取会话自带的 cwd（ACP / 网关会话固定目录）"
        "→ `terminal.cwd`（TERMINAL_CWD）→ Hermes 启动目录\n"
        "    • 支持 `~` 与相对路径（相对 `terminal.cwd` 解析，未设置时相对启动目录）；目录必须已存在；"
        "路径含空格时加引号：`cwd=\"~/my proj\"`\n"
        "    • 再次 `on` 不带 cwd 时沿用上次指定的目录；更换目录会新开 Claude Code 会话，之前的上下文不再保留\n"
        "  mode=default|acceptEdits|plan|auto|bypass — 仅 `on` 可用，Claude Code 的权限模式；"
        "不指定时为 `auto`\n"
        "    • `default`：编辑文件、执行未预授权的命令前都需要授权\n"
        "    • `acceptEdits`：自动接受文件编辑，执行未预授权的命令仍需授权\n"
        "    • `plan`：只规划不改动\n"
        "    • `auto`：由 Claude 自行判断哪些操作需要授权（模型不支持时 Claude Code 退回 acceptEdits）\n"
        "    • `bypass`：⚠️ 跳过所有权限检查，任意命令与文件编辑直接执行\n"
        "    • 每次 `on` 不带 mode 时恢复为 `auto`；切换模式沿用同一个 Claude Code 会话\n"
        "    • 「预授权」指 `~/.claude/settings.json` 等处 `permissions.allow` 里的规则\n"
        "    • 授权：CLI 会弹出 Hermes 审批；飞书 / TUI 等无审批界面的入口，需要授权的操作会被自动拒绝\n"
        "\n"
        "**默认行为**\n"
        "  • 每个会话默认关闭，使用 Hermes 默认 loop\n"
        "  • 开启后同一会话多轮复用同一个 Claude Code 会话；它从空白上下文开始，看不到开启前的 Hermes 对话\n"
        "  • 模型、鉴权（Claude Code 自己的登录）、MCP、hooks、CLAUDE.md 与权限规则均使用本机 `~/.claude` 现有配置；"
        "memory / delegate_task 等 Hermes 工具在该模式下不可用\n"
        "  • 运行中可中断（/stop）或追加消息（steer）；/compress 转为 Claude Code 的 /compact\n"
        "  • 换会话（/new、自动重置）后自动失效\n"
        "  • 与 /codex 互斥：开启 Claude Code 模式会关闭本会话的 Codex 模式，反之亦然\n"
        "  • 需要本机已安装并登录 Claude Code（`claude`）以及 Node.js（npx）；"
        f"适配器默认经 npx 运行 `{DEFAULT_ACP_PACKAGE}`，可用 {ACP_COMMAND_ENV} 覆盖启动命令\n"
        "  • 消息平台：启用 rbac-guard 时仅 admin 可开启；群聊中只有开启者本人的消息走 Claude Code"
    )


def _configured_mode(permission: Optional[str]) -> str:
    return f"`{permission}`" if permission else "沿用 Claude Code 配置（permissions.defaultMode）"


def _approval_text(mode: str, scope: Any) -> str:
    if mode == BYPASS_MODE:
        return "不请求（bypassPermissions）"
    if codex_cmd._approval_bypass_active(scope):
        return "由 Hermes 自动批准（approvals.mode=off / yolo）"
    if scope.surface == "cli":
        return "在 CLI 中弹出 Hermes 审批"
    return "当前入口没有审批界面，需要授权的操作会被自动拒绝"


def _effective_lines(session: ClaudeAcpSession, scope: Any) -> list[str]:
    info = session.info
    mode = info.get("mode") or "default"
    lines = [f"  • 权限模式：`{mode}` — {_MODE_LABELS.get(mode, '见 Claude Code 配置')}"]
    if info.get("model"):
        name = f"（{info['model_name']}）" if info.get("model_name") else ""
        lines.append(f"  • 模型：`{info['model']}`{name}")
    account = info.get("account") or {}
    if account.get("label"):
        lines.append(f"  • 账号：{account['label']}")
    lines.append(f"  • 授权请求：{_approval_text(mode, scope)}")
    return lines


def _describe(scope: Any, mode: Any) -> str:
    session_label = scope.session_id or "（首条消息后创建）"
    note = codex_cmd.other_backend_note(mode, ClaudeBackend)
    if note:
        return f"💤 当前会话 `{session_label}` 未开启 Claude Code 模式。{note}"
    if mode is None:
        return f"💤 当前会话 `{session_label}` 使用 Hermes 默认 loop。发送 /claude on 切到 Claude Code。"
    lines = [
        f"🟢 当前会话 `{session_label}` 已开启 Claude Code 模式（claude-agent-acp）",
        f"  • cwd：`{mode.cwd}`" if mode.cwd else "  • cwd：会话默认工作目录",
        f"  • 权限模式设置：{_configured_mode(mode.backend.permission)}",
    ]
    if mode.owner is not None:
        lines.append(f"  • 开启者：`{mode.owner[0]}:{mode.owner[1]}`（共享会话中仅其消息走 Claude Code）")
    session = mode.codex_session
    if not isinstance(session, ClaudeAcpSession) or not session.started:
        lines.append("  • Claude Code 会话：未启动（下一条消息时启动），权限与模型以届时 Claude Code 报告为准")
        return "\n".join(lines)
    if session.info.get("mode") == BYPASS_MODE:
        lines.append(f"  {_BYPASS_WARNING}")
    lines.append(f"  • Claude Code 会话：`{session.acp_session_id}`（cwd：`{session.cwd}`）")
    used, window = session.info.get("context_used"), session.info.get("context_window")
    if isinstance(used, int) and isinstance(window, int) and window > 0:
        lines.append(f"  • 上下文：{used:,} / {window:,} tokens（{used * 100 // window}%）")
    lines.append("  **实际生效（Claude Code 报告）**")
    return "\n".join(lines + _effective_lines(session, scope))


@dataclass
class _EnablePlan:
    """/claude on 的收尾：启动（或沿用）Claude Code 会话、同步权限模式后再生效。"""

    scope: Any
    mode: Any
    session: ClaudeAcpSession
    started_here: bool
    version: str
    replaced: Any = None
    switched_from: Optional[str] = None  # 被替换的其他后端模式（如 Codex）

    def start(self) -> Optional[str]:
        previous = self.session.permission_mode
        self.session.set_permission_mode(self.mode.backend.permission)
        try:
            self.session.ensure_started()
            self.session.apply_permission_mode()
        except Exception as exc:
            self.session.set_permission_mode(previous)
            return str(exc) or type(exc).__name__
        return None

    def complete(self, error: Optional[str]) -> str:
        if error:
            if self.started_here:
                self.session.close()
            return f"❌ 无法开启 Claude Code 模式：{error}"
        self.mode.codex_session = self.session
        codex_cmd._store_mode(self.mode)
        if self.replaced is not None:
            codex_cmd._close_if_idle(self.replaced)  # cwd 变了或从 Codex 切换：旧线程作废

        info = self.session.info
        lines = [f"✅ 已开启 Claude Code 模式（Claude Code {self.version}，{info.get('agent') or 'claude-agent-acp'}）"]
        if info.get("mode") == BYPASS_MODE:
            lines.append(_BYPASS_WARNING)
        lines += [
            "下一条消息起整轮由本机 Claude Code 执行，结果桥接回本会话；"
            "模型、鉴权、MCP、hooks 与权限规则使用本机 `~/.claude` 现有配置。",
            f"  • cwd：`{self.session.cwd}`",
            f"  • 权限模式设置：{_configured_mode(self.mode.backend.permission)}",
        ]
        if not self.started_here:
            lines.append("  • 沿用本会话已有的 Claude Code 会话，上下文保留。")
        elif info.get("resumed"):
            lines.append("  • 已接上本会话之前的 Claude Code 会话，上下文保留。")
        else:
            lines.append(
                "  • Claude Code 会话从空白上下文开始（看不到此前的 Hermes 对话），之后本会话内多轮复用；"
                "memory / delegate_task 等 Hermes 工具在该模式下不可用。"
            )
        if self.scope.owner is not None:
            lines.append("  • 共享会话（群聊）中只有你的消息走 Claude Code，其他成员仍由 Hermes 处理。")
        if self.switched_from:
            lines.append(f"  • 已关闭本会话的 {self.switched_from} 模式")
        elif self.replaced is not None:
            lines.append(
                "  • 已更换 cwd：已新开 Claude Code 会话，之前的上下文不再保留"
                + ("" if self.replaced.closed else "（旧会话在当前轮结束后关闭）")
            )
        lines.append("**实际生效（Claude Code 报告）**")
        lines += _effective_lines(self.session, self.scope)
        lines.append("发送 /claude off 恢复 Hermes 默认 loop。")
        return "\n".join(lines)


def _plan_enable(scope: Any, options: dict[str, str]):
    denied = codex_cmd._gateway_enable_denied(scope, ClaudeBackend)
    if denied:
        return denied
    ok, version = _claude_code()
    if not ok:
        return f"❌ 无法开启 Claude Code 模式：{version}"

    cwd = None
    if "cwd" in options:
        cwd, err = codex_cmd._resolve_cwd(options["cwd"])
        if err:
            return err
    # 不沿用：每次 on 未指定 mode 都回到默认的 auto
    permission = DEFAULT_MODE
    if "mode" in options:
        key = options["mode"].strip().lower().replace("-", "").replace("_", "")
        permission = _MODE_ALIASES.get(key)
        if permission is None:
            return f"❌ 不支持的 mode：`{options['mode']}`，可选 default / acceptEdits / plan / auto / bypass"
    existing = codex_cmd._lookup_mode(scope)
    if existing is not None and existing.owner not in (None, scope.owner):
        other = codex_cmd.backend_of(existing)
        return f"❌ 当前会话的 {other.title} 模式由其他成员开启，请先由其 /{other.name} off。"
    backend = ClaudeBackend(permission)
    mode = codex_cmd.CodexMode(
        session_id=scope.session_id,
        cwd=cwd or (existing.cwd if existing else None),
        owner=scope.owner,
        gateway_session_key=scope.gateway_session_key,
        codex_session=existing.codex_session if existing else None,
        owner_agent=existing.owner_agent if existing else None,
        backend=backend,
    )

    from agent.runtime_cwd import resolve_agent_cwd

    thread_cwd = mode.cwd or str(resolve_agent_cwd())
    current = mode.codex_session
    if current is not None and not current.closed and backend.reusable(current, thread_cwd, mode):
        return _EnablePlan(scope, mode, current, started_here=False, version=version)
    resume = (
        current.acp_session_id
        if isinstance(current, ClaudeAcpSession) and current.cwd == thread_cwd
        else None
    )
    # 回调在第一轮由 codex_cmd._bind_codex_session 绑定到 agent 线程上
    session = ClaudeAcpSession(cwd=thread_cwd, permission_mode=permission, resume_session_id=resume)
    switched_from = None
    if existing is not None and codex_cmd.backend_of(existing).name != ClaudeBackend.name:
        switched_from = codex_cmd.backend_of(existing).title
    return _EnablePlan(
        scope, mode, session, started_here=True, version=version,
        replaced=current if current is not None and not current.closed else None,
        switched_from=switched_from,
    )


def claude_command(raw_args: str = ""):
    try:
        sub, options, extras = codex_cmd._parse(raw_args)
    except ValueError as exc:
        return f"❌ 参数解析失败：{exc}\n\n{_help_text()}"

    if sub in ("help", "-h", "--help"):
        return _help_text()
    if sub not in ("on", "off", "status"):
        return f"未知子命令：`{sub}`\n\n{_help_text()}"
    unknown = extras + [k for k in options if not (sub == "on" and k in ("cwd", "mode"))]
    if unknown:
        return f"❌ 不支持的参数：{', '.join(unknown)}\n\n{_help_text()}"

    scope, reason = codex_cmd._resolve_scope()
    if scope is None:
        return f"❌ {reason}，/claude 仅作用于当前会话。"

    if sub == "status":
        return _describe(scope, codex_cmd._lookup_mode(scope))

    if sub == "off":
        note = codex_cmd.other_backend_note(codex_cmd._lookup_mode(scope), ClaudeBackend)
        if note:
            return f"当前会话未开启 Claude Code 模式。{note}"
        mode = codex_cmd._drop_mode(scope)
        if mode is None:
            return "当前会话未开启 Claude Code 模式。"
        codex_cmd._release_codex_session(mode)
        return "✅ 已关闭 Claude Code 模式，下一条消息起恢复 Hermes 默认 loop。"

    plan = _plan_enable(scope, options)
    if not isinstance(plan, _EnablePlan):
        return plan
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return plan.complete(plan.start())

    async def _enable_off_loop() -> str:
        # gateway 在事件循环上直接调用插件命令：Claude Code 启动放到线程里，免得卡住其他会话
        return plan.complete(await asyncio.to_thread(plan.start))

    return _enable_off_loop()


def register(ctx):
    # pre_llm_call / pre_gateway_dispatch / on_session_finalize 钩子由 codex_cmd 注册，两种模式共用
    ctx.register_command(
        name="claude",
        handler=claude_command,
        description=(
            "当前会话切换到本机 Claude Code（经 claude-agent-acp）执行整轮对话，"
            "会话级开关，不修改任何配置"
        ),
        args_hint="on [cwd=DIR] [mode=default|acceptEdits|plan|auto|bypass] | off | status | help",
    )
