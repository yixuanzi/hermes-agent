"""ext-tools /claude：会话级切换到本机 Claude Code（经 claude-agent-acp）。

ACP 侧用 fake_claude_acp_agent.py 子进程顶替真实 claude-agent-acp。
"""

import json
import os
import shlex
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "plugins" / "ext-tools"))

import claude_acp  # noqa: E402
import claude_cmd  # noqa: E402
import codex_cmd  # noqa: E402
from agent.subagent_lifecycle import bind_subagent_parent  # noqa: E402
from claude_acp import ClaudeAcpSession  # noqa: E402
from tools.user_env_runtime import (  # noqa: E402
    reset_current_user_env_identity,
    set_current_user_env_identity,
)

FAKE_AGENT = Path(__file__).with_name("fake_claude_acp_agent.py")


class FakeAgent:
    def __init__(self, session_id="sess-1", api_mode="chat_completions"):
        self.session_id = session_id
        self.api_mode = api_mode
        self.provider = "custom"
        self.model = "m"
        self.base_url = "https://example.invalid/v1"
        self.api_key = "k"
        self.auth_mode = ""
        self.requested_provider = "custom"
        self._codex_session = None
        self.deltas = []

    def _fire_stream_delta(self, text):
        self.deltas.append(text)

    def _current_main_runtime(self):
        return {"provider": self.provider, "api_mode": self.api_mode}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    codex_cmd._modes_by_sid.clear()
    codex_cmd._pending_by_gateway_key.clear()
    codex_cmd._gateway_dispatch.set(None)
    monkeypatch.setenv(claude_acp.ACP_COMMAND_ENV, f"{shlex.quote(sys.executable)} {shlex.quote(str(FAKE_AGENT))}")
    monkeypatch.setenv("CLAUDE_CODE_EXECUTABLE", sys.executable)
    monkeypatch.setenv("FAKE_ACP_LOG", str(tmp_path / "acp.jsonl"))
    monkeypatch.setattr(codex_cmd, "_publish_aux_runtime", lambda agent, mode: None)
    monkeypatch.setattr(codex_cmd, "_rbac_role", lambda platform, uid: None)
    monkeypatch.setattr(codex_cmd, "_approval_bypass_active", lambda scope: False)
    monkeypatch.setattr(codex_cmd, "_codex_binary", lambda: (True, "0.144.1"))
    monkeypatch.setattr(claude_cmd, "_claude_code", lambda: (True, "2.1.286"))
    monkeypatch.delitem(sys.modules, "tui_gateway.server", raising=False)
    yield
    for mode in list(codex_cmd._modes_by_sid.values()):
        session = mode.codex_session
        if session is not None:
            session.close()
    codex_cmd._modes_by_sid.clear()
    codex_cmd._gateway_dispatch.set(None)


@pytest.fixture
def fake_codex(monkeypatch):
    from agent.transports.codex_app_server_session import CodexAppServerSession

    def _start(self):
        self._effective_policy = {"sandbox": "workspaceWrite", "approval_policy": "on-request",
                                  "network_access": True, "writable_roots": []}
        self._thread_id = "thread-test"
        return self._thread_id

    monkeypatch.setattr(CodexAppServerSession, "ensure_started", _start)


def _use_cli(monkeypatch, agent):
    import hermes_cli.plugins as plugins_mod

    cli = SimpleNamespace(agent=agent, session_id=getattr(agent, "session_id", None))
    monkeypatch.setattr(plugins_mod, "get_plugin_manager", lambda: SimpleNamespace(_cli_ref=cli))


def _turn(agent, **kwargs):
    with bind_subagent_parent(agent):
        codex_cmd.on_pre_llm_call(session_id=agent.session_id, **kwargs)


def _session(sid="sess-cli") -> ClaudeAcpSession:
    return codex_cmd.get_mode(sid).codex_session


# ------------------------------------------------------------ command ----
def test_help_and_bad_args(monkeypatch):
    import hermes_cli.plugins as plugins_mod

    monkeypatch.setattr(plugins_mod, "get_plugin_manager", lambda: SimpleNamespace(_cli_ref=None))
    text = claude_cmd.claude_command("")
    assert text == claude_cmd.claude_command("help")
    for fragment in ("/claude on [cwd=DIR] [mode=default|acceptEdits|plan|auto|bypass]", "/claude off",
                     "/claude status", "不指定时为 `auto`", "`bypass`：⚠️", "与 /codex 互斥",
                     claude_acp.DEFAULT_ACP_PACKAGE, "每个会话默认关闭"):
        assert fragment in text, fragment
    assert "未知子命令" in claude_cmd.claude_command("maybe")
    assert "不支持的参数" in claude_cmd.claude_command("off mode=plan")
    assert "不支持的参数" in claude_cmd.claude_command("on sandbox=full")
    assert claude_cmd.claude_command("on").startswith("❌")  # 识别不到会话：fail-closed
    assert not codex_cmd._modes_by_sid


def test_replies_never_become_gateway_file_attachments(monkeypatch, tmp_path):
    from gateway.platforms.base import BasePlatformAdapter

    # 网关把回复正文里真实存在的 ~/….json 等路径当附件发送（飞书上会多收到一个 settings.json）
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    _use_cli(monkeypatch, FakeAgent("sess-cli"))
    replies = [claude_cmd.claude_command(""), claude_cmd.claude_command(f"on cwd={tmp_path}"),
               claude_cmd.claude_command("status"), claude_cmd.claude_command("off"), codex_cmd._help_text()]
    for text in replies:
        assert BasePlatformAdapter.extract_local_files(text)[0] == [], text


def test_cli_on_reports_claude_code_state_then_status_and_off(monkeypatch, tmp_path):
    _use_cli(monkeypatch, FakeAgent("sess-cli"))
    text = claude_cmd.claude_command(f"on cwd={tmp_path}")
    for fragment in ("已开启 Claude Code 模式（Claude Code 2.1.286，fake-claude-acp 9.9.9）",
                     f"cwd：`{tmp_path.resolve()}`", "权限模式设置：`auto`", "从空白上下文开始",
                     "权限模式：`auto`", "模型：`sonnet`（Sonnet 5.5）", "账号：Claude Team",
                     "在 CLI 中弹出 Hermes 审批"):
        assert fragment in text, fragment
    mode = codex_cmd.get_mode("sess-cli")
    assert isinstance(mode.backend, claude_cmd.ClaudeBackend) and mode.owner is None
    session = mode.codex_session
    assert session.started and session.acp_session_id == "new-session"

    status = claude_cmd.claude_command("status")
    assert "已开启 Claude Code 模式" in status and "`new-session`" in status and "实际生效" in status

    assert "已关闭" in claude_cmd.claude_command("off")
    assert session.closed and codex_cmd.get_mode("sess-cli") is None
    assert "未开启" in claude_cmd.claude_command("off")
    assert "Hermes 默认 loop" in claude_cmd.claude_command("status")


def test_mode_option_and_reuse(monkeypatch):
    _use_cli(monkeypatch, FakeAgent("sess-cli"))
    assert "不支持的 mode" in claude_cmd.claude_command("on mode=wide")
    assert codex_cmd.get_mode("sess-cli") is None

    text = claude_cmd.claude_command("on mode=plan")
    assert "权限模式设置：`plan`" in text and "权限模式：`plan`" in text
    session = _session()

    text = claude_cmd.claude_command("on")  # 同 cwd 再次 on：沿用会话，mode 不沿用
    assert _session() is session and not session.closed
    assert "沿用本会话已有的 Claude Code 会话" in text and "权限模式：`auto`" in text

    text = claude_cmd.claude_command("on mode=BYPASS")
    assert claude_cmd._BYPASS_WARNING in text and "授权请求：不请求" in text
    assert claude_cmd._BYPASS_WARNING in claude_cmd.claude_command("status")


def test_start_failure_does_not_enable(monkeypatch):
    monkeypatch.setenv("FAKE_ACP_AUTH", "none")
    _use_cli(monkeypatch, FakeAgent("sess-cli"))
    text = claude_cmd.claude_command("on")
    assert "无法开启 Claude Code 模式" in text and "/login" in text
    assert codex_cmd.get_mode("sess-cli") is None

    monkeypatch.setattr(claude_cmd, "_claude_code", lambda: (False, "未找到本机 claude CLI"))
    assert "未找到本机 claude CLI" in claude_cmd.claude_command("on")


# ------------------------------------------------------- runtime switch ----
def test_turn_routes_to_claude_session_and_back(monkeypatch, tmp_path):
    agent = FakeAgent("sess-cli")
    _use_cli(monkeypatch, agent)
    claude_cmd.claude_command(f"on cwd={tmp_path}")
    prestarted = _session()

    _turn(agent)
    assert agent.api_mode == "codex_app_server"
    assert agent._codex_session is prestarted  # 第一轮直接复用 on 时启动的会话
    assert agent.session_cwd == str(tmp_path.resolve())
    assert agent._current_main_runtime()["api_mode"] == "chat_completions"
    # 显示回调已绑定到 agent
    prestarted._on_event({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "hi"}})
    assert agent.deltas == ["hi"]

    claude_cmd.claude_command("off")
    _turn(agent)
    assert agent.api_mode == "chat_completions" and agent._codex_session is None
    assert "session_cwd" not in agent.__dict__


def test_rebuilt_agent_reuses_claude_session(monkeypatch):
    first = FakeAgent("sess-gw")
    _use_cli(monkeypatch, first)
    claude_cmd.claude_command("on")
    _turn(first)
    session = first._codex_session

    rebuilt = FakeAgent("sess-gw")
    _turn(rebuilt)
    assert rebuilt._codex_session is session and first._codex_session is None
    session._on_event({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "x"}})
    assert rebuilt.deltas == ["x"] and first.deltas == []


def test_retired_session_is_resumed(monkeypatch):
    agent = FakeAgent("sess-cli")
    _use_cli(monkeypatch, agent)
    claude_cmd.claude_command("on")
    _turn(agent)
    old = agent._codex_session
    old.close()  # runtime 在回合崩溃 / 卡死时退役会话
    agent._codex_session = None
    _turn(agent)
    replacement = agent._codex_session
    assert replacement is not old and not replacement.closed
    assert replacement._resume_session_id == old.acp_session_id == "new-session"


def test_cwd_change_opens_fresh_session(monkeypatch, tmp_path):
    agent = FakeAgent("sess-cli")
    _use_cli(monkeypatch, agent)
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    claude_cmd.claude_command(f"on cwd={first}")
    _turn(agent)
    old = agent._codex_session
    assert "已更换 cwd" in claude_cmd.claude_command(f"on cwd={second}")
    _turn(agent)
    assert old.closed and agent._codex_session.cwd == str(second.resolve())
    assert agent._codex_session._resume_session_id is None


# ----------------------------------------------------- codex exclusivity ----
def test_claude_and_codex_replace_each_other(monkeypatch, fake_codex):
    agent = FakeAgent("sess-cli")
    _use_cli(monkeypatch, agent)
    codex_cmd.codex_command("on")
    _turn(agent)
    codex_thread = agent._codex_session

    text = claude_cmd.claude_command("on")
    assert "已关闭本会话的 Codex 模式" in text and codex_thread.closed
    assert "当前会话已开启 Claude Code 模式" in codex_cmd.codex_command("status")
    assert "/claude off" in codex_cmd.codex_command("off")
    assert codex_cmd.get_mode("sess-cli") is not None  # /codex off 不关别人的模式
    _turn(agent)
    claude_session = agent._codex_session
    assert isinstance(claude_session, ClaudeAcpSession)

    text = codex_cmd.codex_command("on")
    assert "已关闭本会话的 Claude Code 模式" in text and claude_session.closed
    assert "当前会话已开启 Codex 模式" in claude_cmd.claude_command("status")
    _turn(agent)
    assert not isinstance(agent._codex_session, ClaudeAcpSession)
    assert agent.api_mode == "codex_app_server"


# -------------------------------------------------------------- gateway ----
class FakeRunner:
    def __init__(self, session_id):
        self.session_store = SimpleNamespace(peek_session_id=lambda key: session_id)

    def _session_key_for_source(self, source):
        return f"agent:main:feishu:group:{source.chat_id}"


def _gateway_command(runner, raw_args, *, uid="ou_admin", chat="oc_1"):
    source = SimpleNamespace(platform=SimpleNamespace(value="feishu"), user_id=uid, chat_id=chat)
    event = SimpleNamespace(get_command=lambda: "claude", source=source)
    codex_cmd.on_pre_gateway_dispatch(event=event, gateway=runner)
    token = set_current_user_env_identity("feishu", uid, "name")
    try:
        result = claude_cmd.claude_command(raw_args)
        if hasattr(result, "__await__"):
            import asyncio

            result = asyncio.run(result)
        return result
    finally:
        reset_current_user_env_identity(token)


def test_gateway_owner_only_and_auto_deny_notice(monkeypatch):
    import hermes_cli.plugins as plugins_mod

    monkeypatch.setattr(plugins_mod, "get_plugin_manager", lambda: SimpleNamespace(_cli_ref=None))
    runner = FakeRunner("sess-gw")
    text = _gateway_command(runner, "on")
    assert "只有你的消息走 Claude Code" in text and "自动拒绝" in text
    assert codex_cmd.get_mode("sess-gw").owner == ("feishu", "ou_admin")

    agent = FakeAgent("sess-gw")
    _turn(agent, platform="feishu", sender_id="ou_other")
    assert agent.api_mode == "chat_completions"
    _turn(agent, platform="feishu", sender_id="ou_admin")
    assert isinstance(agent._codex_session, ClaudeAcpSession)
    assert "其他成员开启" in _gateway_command(runner, "on", uid="ou_second")


def test_gateway_requires_rbac_admin(monkeypatch):
    import hermes_cli.plugins as plugins_mod

    monkeypatch.setattr(plugins_mod, "get_plugin_manager", lambda: SimpleNamespace(_cli_ref=None))
    monkeypatch.setattr(codex_cmd, "_rbac_role", lambda platform, uid: "user")
    assert "仅 admin 可开启 Claude Code 模式" in _gateway_command(FakeRunner("sess-gw"), "on")
    assert codex_cmd.get_mode("sess-gw") is None


def test_on_inside_event_loop_starts_off_loop(monkeypatch):
    import asyncio

    import hermes_cli.plugins as plugins_mod

    monkeypatch.setattr(plugins_mod, "get_plugin_manager", lambda: SimpleNamespace(_cli_ref=None))
    threads = []
    original = ClaudeAcpSession.ensure_started

    def _record(self):
        threads.append(threading.current_thread())
        return original(self)

    monkeypatch.setattr(ClaudeAcpSession, "ensure_started", _record)

    runner = FakeRunner("sess-gw")  # 插件只持有 runner 的弱引用

    async def _gateway_turn():
        source = SimpleNamespace(platform=SimpleNamespace(value="feishu"), user_id="ou_admin", chat_id="oc_1")
        codex_cmd.on_pre_gateway_dispatch(
            event=SimpleNamespace(get_command=lambda: "claude", source=source), gateway=runner,
        )
        token = set_current_user_env_identity("feishu", "ou_admin", "name")
        try:
            result = claude_cmd.claude_command("on")
        finally:
            reset_current_user_env_identity(token)
        assert asyncio.iscoroutine(result)
        return await result

    assert "已开启 Claude Code 模式" in asyncio.run(_gateway_turn())
    assert threads and threads[0] is not threading.main_thread()


# ------------------------------------------------- AIAgent integration ----
@pytest.fixture
def real_agent(monkeypatch):
    import hermes_cli.lifecycle as lifecycle
    import run_agent

    def _invoke(name, **kwargs):
        if name == "pre_llm_call":
            codex_cmd.on_pre_llm_call(**kwargs)
        return []

    monkeypatch.setattr(lifecycle, "invoke_hook", _invoke)
    agent = run_agent.AIAgent(
        api_key="stub", base_url="https://stub.invalid", provider="openai", api_mode="chat_completions",
        quiet_mode=True, skip_context_files=True, skip_memory=True,
    )
    _use_cli(monkeypatch, agent)
    with patch.object(agent, "_spawn_background_review", return_value=None):
        yield agent


def test_real_agent_turn_runs_through_core_codex_path(real_agent, tmp_path):
    from tools.terminal_tool import set_approval_callback

    assert "已开启" in claude_cmd.claude_command(f"on cwd={tmp_path}")
    set_approval_callback(lambda *a, **k: "once")
    try:
        result = real_agent.run_conversation("tool")
    finally:
        set_approval_callback(None)

    assert real_agent.api_mode == "codex_app_server"
    assert result["final_response"] == "All done."
    assert result["completed"] and not result["interrupted"]
    contents = [(m["role"], m.get("content")) for m in result["messages"]]
    assert contents[-4:] == [("assistant", "Checking."), ("assistant", None), ("tool", "hi"), ("assistant", "All done.")]
    assert [m["role"] for m in result["messages"]].count("user") == 1  # 用户消息不重复
    assert result["codex_thread_id"] == "new-session"
    assert real_agent.session_total_tokens == 135

    second = real_agent.run_conversation("hello again")  # 同一个 Claude Code 会话继续
    assert second["final_response"] == "mode=auto echo=hello again"
    assert second["codex_thread_id"] == "new-session"


def test_real_agent_interrupt_and_redirect_reach_claude(real_agent, tmp_path):
    claude_cmd.claude_command(f"on cwd={tmp_path}")
    threading.Timer(0.5, real_agent.interrupt).start()
    result = real_agent.run_conversation("slow")
    assert result["interrupted"] or result["partial"]

    accepted = []
    threading.Timer(0.5, lambda: accepted.append(real_agent.redirect("go left"))).start()
    result = real_agent.run_conversation("slow")
    assert accepted == [True]
    assert result["final_response"] == "Working steered:go left"


def test_model_lists_models_and_effort_then_sets(monkeypatch):
    _use_cli(monkeypatch, FakeAgent("sess-cli"))
    assert "未开启" in claude_cmd.claude_command("model")

    claude_cmd.claude_command("on")
    text = claude_cmd.claude_command("model")
    assert "▶ `sonnet`（Sonnet 5.5）" in text and "`opus`（Opus 5.5）" in text
    assert "▶ `medium`" in text and "`low`" in text and "`high`" in text

    text = claude_cmd.claude_command("model set effort=high")
    assert "effort → `high`" in text and "▶ `high`" in text
    assert _session().preferences == {"effort": "high"}

    # 换到不支持 effort 的模型：effort 选项消失，之前的 effort 偏好作废
    text = claude_cmd.claude_command("model set opus")
    assert "model → `opus`" in text and "当前模型不支持" in text
    assert _session().preferences == {"model": "opus"}
    assert "不支持思考深度" in claude_cmd.claude_command("model set effort=low")

    text = claude_cmd.claude_command("model set model=sonnet effort=low")
    assert "model → `sonnet`" in text and "effort → `low`" in text


def test_model_set_rejects_bad_input_and_busy_turn(monkeypatch):
    _use_cli(monkeypatch, FakeAgent("sess-cli"))
    claude_cmd.claude_command("on")
    assert "用法" in claude_cmd.claude_command("model set")
    assert "用法" in claude_cmd.claude_command("model set color=red")
    assert "参数有误" in claude_cmd.claude_command("model set opus model=sonnet")
    assert "不支持的参数" in claude_cmd.claude_command("model foo")
    text = claude_cmd.claude_command("model set model=nope")
    assert "设置 model 失败" in text and "Invalid value" in text
    assert _session().preferences == {} and _session().info["model"] == "sonnet"

    session = _session()
    with patch.object(ClaudeAcpSession, "turn_active", new=True):
        assert "正在执行" in claude_cmd.claude_command("model set opus")
    assert session.info["model"] == "sonnet"


def test_model_preferences_survive_session_resume(monkeypatch):
    session = ClaudeAcpSession(cwd=str(Path.cwd()), resume_session_id="known-session",
                               preferences={"model": "sonnet", "effort": "high"})
    try:
        session.ensure_started()
        assert session.info["effort"] == "high" and session.info["resumed"]
    finally:
        session.close()


def test_compact_subcommand(monkeypatch):
    _use_cli(monkeypatch, FakeAgent("sess-cli"))
    assert "未开启" in claude_cmd.claude_command("compact")
    claude_cmd.claude_command("on")
    assert "不支持的参数" in claude_cmd.claude_command("compact now")

    session = _session()
    with patch.object(ClaudeAcpSession, "turn_active", new=True):
        assert "正在执行" in claude_cmd.claude_command("compact")
    text = claude_cmd.claude_command("compact")
    assert "已让 Claude Code 压缩上下文" in text
    assert not session.closed
    prompts = [json.loads(l) for l in Path(os.environ["FAKE_ACP_LOG"]).read_text().splitlines()]
    assert [m["params"]["prompt"][0]["text"] for m in prompts if m.get("method") == "session/prompt"] == ["/compact"]


def test_new_opens_fresh_session_keeping_cwd_mode_and_preferences(monkeypatch, tmp_path):
    agent = FakeAgent("sess-cli")
    _use_cli(monkeypatch, agent)
    assert "未开启" in claude_cmd.claude_command("new")
    claude_cmd.claude_command(f"on cwd={tmp_path} mode=plan")
    claude_cmd.claude_command("model set effort=high")
    old = _session()

    text = claude_cmd.claude_command("new")
    fresh = _session()
    assert "已开启新的 Claude Code 会话" in text and "plan" in text
    assert fresh is not old and old.closed and not fresh.closed
    assert not fresh.info.get("resumed")  # 全新上下文，不 resume 旧会话
    assert fresh.cwd == str(tmp_path.resolve())
    assert fresh.preferences == {"effort": "high"}
    _turn(agent)  # 下一轮把新会话绑到 agent 上
    assert agent._codex_session is fresh


def test_new_refused_during_turn_and_keeps_old_session(monkeypatch):
    _use_cli(monkeypatch, FakeAgent("sess-cli"))
    claude_cmd.claude_command("on")
    old = _session()
    with patch.object(ClaudeAcpSession, "turn_active", new=True):
        assert "/stop" in claude_cmd.claude_command("new")
    assert _session() is old and not old.closed
    assert "不支持的参数" in claude_cmd.claude_command("new now")


def test_new_start_failure_keeps_old_session(monkeypatch):
    _use_cli(monkeypatch, FakeAgent("sess-cli"))
    claude_cmd.claude_command("on")
    old = _session()

    def _boom(self):
        raise RuntimeError("spawn failed")

    monkeypatch.setattr(ClaudeAcpSession, "ensure_started", _boom)
    assert "spawn failed" in claude_cmd.claude_command("new")
    assert _session() is old and not old.closed


def test_stop_interrupts_only_an_active_turn(monkeypatch):
    _use_cli(monkeypatch, FakeAgent("sess-cli"))
    assert "未开启" in claude_cmd.claude_command("stop")
    claude_cmd.claude_command("on")
    session = _session()
    assert "没有正在执行" in claude_cmd.claude_command("stop")

    with patch.object(ClaudeAcpSession, "turn_active", new=True), \
            patch.object(ClaudeAcpSession, "request_interrupt") as interrupt:
        assert "已请求中断" in claude_cmd.claude_command("stop")
    interrupt.assert_called_once_with()
    assert codex_cmd.get_mode("sess-cli") is not None and not session.closed
