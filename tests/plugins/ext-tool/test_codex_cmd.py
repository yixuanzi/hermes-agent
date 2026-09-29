"""ext-tools /codex：会话级切换到 codex app-server 运行时。"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "plugins" / "ext-tools"))

import codex_cmd  # noqa: E402
from agent.subagent_lifecycle import bind_subagent_parent  # noqa: E402
from tools.user_env_runtime import (  # noqa: E402
    reset_current_user_env_identity,
    set_current_user_env_identity,
)


class FakeAgent:
    def __init__(self, session_id="sess-1", api_mode="codex_responses"):
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
def _isolated(monkeypatch):
    codex_cmd._modes_by_sid.clear()
    codex_cmd._pending_by_gateway_key.clear()
    codex_cmd._gateway_dispatch.set(None)
    published = []
    monkeypatch.setattr(
        codex_cmd, "_publish_aux_runtime", lambda agent, mode: published.append(mode)
    )
    monkeypatch.setattr(codex_cmd, "_codex_binary", lambda: (True, "0.144.1"))
    monkeypatch.setattr(codex_cmd, "_rbac_role", lambda platform, uid: None)
    monkeypatch.delitem(sys.modules, "tui_gateway.server", raising=False)
    yield published
    codex_cmd._gateway_dispatch.set(None)


def _use_cli(monkeypatch, agent):
    import hermes_cli.plugins as plugins_mod

    cli = SimpleNamespace(agent=agent, session_id=getattr(agent, "session_id", None))
    monkeypatch.setattr(
        plugins_mod, "get_plugin_manager", lambda: SimpleNamespace(_cli_ref=cli)
    )
    return cli


def _turn(agent, **kwargs):
    with bind_subagent_parent(agent):
        codex_cmd.on_pre_llm_call(session_id=agent.session_id, **kwargs)


# ------------------------------------------------------------ command ----
def test_help_unknown_and_bad_args():
    assert "/codex on" in codex_cmd.codex_command("help")
    assert "未知子命令" in codex_cmd.codex_command("maybe")
    assert "不支持的参数" in codex_cmd.codex_command("off cwd=/tmp")
    assert "不支持的参数" in codex_cmd.codex_command("on extra")


def test_without_session_fails_closed(monkeypatch):
    import hermes_cli.plugins as plugins_mod

    monkeypatch.setattr(
        plugins_mod, "get_plugin_manager", lambda: SimpleNamespace(_cli_ref=None)
    )
    assert codex_cmd.codex_command("on").startswith("❌")
    assert not codex_cmd._modes_by_sid


def test_cli_on_status_off(monkeypatch, tmp_path):
    _use_cli(monkeypatch, FakeAgent("sess-cli"))

    assert "已开启 Codex 模式" in codex_cmd.codex_command(f"on cwd={tmp_path}")
    mode = codex_cmd.get_mode("sess-cli")
    assert mode.cwd == str(tmp_path.resolve()) and mode.owner is None
    assert "已开启 Codex 模式" in codex_cmd.codex_command("status")

    # on 不带 cwd 时保留之前指定的目录
    codex_cmd.codex_command("on")
    assert codex_cmd.get_mode("sess-cli").cwd == str(tmp_path.resolve())

    assert "已关闭" in codex_cmd.codex_command("off")
    assert codex_cmd.get_mode("sess-cli") is None
    assert "未开启" in codex_cmd.codex_command("off")
    assert "Hermes 默认 loop" in codex_cmd.codex_command("")


def test_on_rejects_missing_cwd_and_missing_codex(monkeypatch, tmp_path):
    _use_cli(monkeypatch, FakeAgent("sess-cli"))
    assert "不是已存在的目录" in codex_cmd.codex_command(f"on cwd={tmp_path / 'nope'}")

    monkeypatch.setattr(codex_cmd, "_codex_binary", lambda: (False, "codex CLI not found"))
    assert "codex CLI not found" in codex_cmd.codex_command("on")
    assert codex_cmd.get_mode("sess-cli") is None


# ------------------------------------------------------- runtime switch ----
def test_turn_switches_to_codex_and_back(monkeypatch, tmp_path, _isolated):
    agent = FakeAgent("sess-cli")
    _use_cli(monkeypatch, agent)
    codex_cmd.codex_command(f"on cwd={tmp_path}")

    _turn(agent)
    assert agent.api_mode == "codex_app_server"
    assert agent.session_cwd == str(tmp_path.resolve())
    # 辅助调用与 review fork 仍按宿主协议路由
    assert agent._current_main_runtime()["api_mode"] == "codex_responses"
    assert _isolated[-1] == "codex_responses"
    # 线程由插件在轮次边界创建（尚未 spawn），cwd 为会话指定目录
    session = agent._codex_session
    assert session is not None and session.cwd == str(tmp_path.resolve())
    assert codex_cmd.get_mode("sess-cli").codex_session is session

    _turn(agent)  # 后续轮次复用同一线程
    assert agent._codex_session is session

    codex_cmd.codex_command("off")
    assert session.closed  # 空闲时 off 立即关闭线程
    _turn(agent)
    assert agent.api_mode == "codex_responses"
    assert "session_cwd" not in agent.__dict__
    assert "_current_main_runtime" not in agent.__dict__
    assert agent._codex_session is None


def test_rebuilt_agent_reuses_codex_thread(monkeypatch):
    """gateway 重建 AIAgent（同一 Hermes 会话）时沿用同一个 codex 线程。"""
    first = FakeAgent("sess-gw")
    _use_cli(monkeypatch, first)
    codex_cmd.codex_command("on")
    _turn(first)
    session = first._codex_session

    rebuilt = FakeAgent("sess-gw")
    _turn(rebuilt)
    assert rebuilt._codex_session is session
    assert first._codex_session is None  # 旧 agent 放手，关闭它不会连带关线程
    assert not session.closed
    # 线程的显示回调已改绑到新 agent
    session._on_event({"method": "item/agentMessage/delta", "params": {"delta": "hi"}})
    assert rebuilt.deltas == ["hi"] and first.deltas == []


def test_retired_codex_thread_is_replaced(monkeypatch):
    agent = FakeAgent("sess-cli")
    _use_cli(monkeypatch, agent)
    codex_cmd.codex_command("on")
    _turn(agent)
    old = agent._codex_session
    old.close()  # runtime 在 turn 崩溃/超时时退役线程
    _turn(agent)
    assert agent._codex_session is not old and not agent._codex_session.closed


def test_off_during_active_turn_defers_close(monkeypatch):
    agent = FakeAgent("sess-cli")
    _use_cli(monkeypatch, agent)
    codex_cmd.codex_command("on")
    _turn(agent)
    session = agent._codex_session
    session._active_turn_id = "turn-1"
    codex_cmd.codex_command("off")
    assert not session.closed
    session._active_turn_id = None
    _turn(agent)  # 下一轮边界收尾
    assert session.closed and agent._codex_session is None


def test_cwd_change_retires_codex_thread(monkeypatch, tmp_path):
    agent = FakeAgent("sess-cli")
    _use_cli(monkeypatch, agent)
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    codex_cmd.codex_command(f"on cwd={first}")
    _turn(agent)
    session = agent._codex_session

    codex_cmd.codex_command(f"on cwd={second}")
    _turn(agent)
    assert session.closed
    assert agent._codex_session.cwd == str(second.resolve())
    assert agent.session_cwd == str(second.resolve())


def test_host_session_cwd_restored(monkeypatch, tmp_path):
    agent = FakeAgent("sess-acp")
    agent.session_cwd = "/host/project"
    _use_cli(monkeypatch, agent)
    codex_cmd.codex_command(f"on cwd={tmp_path}")
    _turn(agent)
    codex_cmd.codex_command("off")
    _turn(agent)
    assert agent.session_cwd == "/host/project"


def test_new_session_restores_host_runtime(monkeypatch):
    agent = FakeAgent("sess-old")
    _use_cli(monkeypatch, agent)
    codex_cmd.codex_command("on")
    _turn(agent)
    assert agent.api_mode == "codex_app_server"

    agent.session_id = "sess-new"  # /new 复用同一个 agent 对象
    _turn(agent)
    assert agent.api_mode == "codex_responses"


def test_host_model_switch_is_respected(monkeypatch):
    agent = FakeAgent("sess-cli")
    _use_cli(monkeypatch, agent)
    codex_cmd.codex_command("on")
    _turn(agent)
    agent.api_mode = "chat_completions"  # 期间 /model 切到了别的 provider
    _turn(agent)
    assert agent.api_mode == "codex_app_server"
    codex_cmd.codex_command("off")
    _turn(agent)
    assert agent.api_mode == "chat_completions"


def test_background_review_fork_stays_on_hermes(monkeypatch):
    agent = FakeAgent("sess-cli")
    _use_cli(monkeypatch, agent)
    codex_cmd.codex_command("on")
    fork = FakeAgent("sess-cli")
    fork._memory_write_origin = "background_review"
    _turn(fork, parent_session_id="sess-cli")
    assert fork.api_mode == "codex_responses"


def test_native_global_runtime_is_left_alone(monkeypatch):
    agent = FakeAgent("sess-cli", api_mode="codex_app_server")
    _use_cli(monkeypatch, agent)
    codex_cmd.codex_command("on")
    _turn(agent)
    codex_cmd.codex_command("off")
    _turn(agent)
    assert agent.api_mode == "codex_app_server"
    assert "_ext_codex_host" not in agent.__dict__


def test_hook_ignores_mismatched_bound_agent(monkeypatch):
    agent = FakeAgent("sess-cli")
    _use_cli(monkeypatch, agent)
    codex_cmd.codex_command("on")
    with bind_subagent_parent(agent):
        codex_cmd.on_pre_llm_call(session_id="other-session")
    assert agent.api_mode == "codex_responses"


# -------------------------------------------------------------- gateway ----
class FakeRunner:
    def __init__(self, session_id):
        self.session_store = SimpleNamespace(peek_session_id=lambda key: session_id)

    def _session_key_for_source(self, source):
        return f"agent:main:feishu:group:{source.chat_id}"


def _gateway_command(runner, raw_args, *, uid="ou_admin", chat="oc_1"):
    source = SimpleNamespace(platform=SimpleNamespace(value="feishu"), user_id=uid, chat_id=chat)
    event = SimpleNamespace(get_command=lambda: "codex", source=source)
    codex_cmd.on_pre_gateway_dispatch(event=event, gateway=runner)
    token = set_current_user_env_identity("feishu", uid, "name")
    try:
        return codex_cmd.codex_command(raw_args)
    finally:
        reset_current_user_env_identity(token)


def test_gateway_owner_only_turns(monkeypatch):
    import hermes_cli.plugins as plugins_mod

    monkeypatch.setattr(plugins_mod, "get_plugin_manager", lambda: SimpleNamespace(_cli_ref=None))
    runner = FakeRunner("sess-gw")
    assert "只有你的消息走 Codex" in _gateway_command(runner, "on")
    assert codex_cmd.get_mode("sess-gw").owner == ("feishu", "ou_admin")

    agent = FakeAgent("sess-gw")
    _turn(agent, platform="feishu", sender_id="ou_other")
    assert agent.api_mode == "codex_responses"
    _turn(agent, platform="feishu", sender_id="ou_admin")
    assert agent.api_mode == "codex_app_server"
    session = agent._codex_session
    _turn(agent, platform="feishu", sender_id="ou_other")
    assert agent.api_mode == "codex_responses"
    assert agent._codex_session is session  # 非开启者轮次不关闭开启者的 codex 线程

    assert "其他成员开启" in _gateway_command(runner, "on", uid="ou_second")


def test_gateway_pending_session_binds_on_first_turn(monkeypatch):
    import hermes_cli.plugins as plugins_mod

    monkeypatch.setattr(plugins_mod, "get_plugin_manager", lambda: SimpleNamespace(_cli_ref=None))
    _gateway_command(FakeRunner(None), "on")
    assert "agent:main:feishu:group:oc_1" in codex_cmd._pending_by_gateway_key

    agent = FakeAgent("sess-first")
    agent._gateway_session_key = "agent:main:feishu:group:oc_1"
    _turn(agent, platform="feishu", sender_id="ou_admin")
    assert agent.api_mode == "codex_app_server"
    assert codex_cmd.get_mode("sess-first") is not None
    assert not codex_cmd._pending_by_gateway_key


def test_gateway_requires_rbac_admin(monkeypatch):
    import hermes_cli.plugins as plugins_mod

    monkeypatch.setattr(plugins_mod, "get_plugin_manager", lambda: SimpleNamespace(_cli_ref=None))
    monkeypatch.setattr(codex_cmd, "_rbac_role", lambda platform, uid: "user")
    assert "仅 admin" in _gateway_command(FakeRunner("sess-gw"), "on")
    assert codex_cmd.get_mode("sess-gw") is None

    def _broken(platform, uid):
        raise RuntimeError("db locked")

    monkeypatch.setattr(codex_cmd, "_rbac_role", _broken)
    assert "无法确认你的角色" in _gateway_command(FakeRunner("sess-gw"), "on")


def test_gateway_capture_must_match_caller(monkeypatch):
    import hermes_cli.plugins as plugins_mod

    monkeypatch.setattr(plugins_mod, "get_plugin_manager", lambda: SimpleNamespace(_cli_ref=None))
    source = SimpleNamespace(platform=SimpleNamespace(value="feishu"), user_id="ou_a", chat_id="oc_1")
    event = SimpleNamespace(get_command=lambda: "codex", source=source)
    codex_cmd.on_pre_gateway_dispatch(event=event, gateway=FakeRunner("sess-gw"))
    token = set_current_user_env_identity("feishu", "ou_b", "b")
    try:
        assert codex_cmd.codex_command("on").startswith("❌")
    finally:
        reset_current_user_env_identity(token)
    assert codex_cmd.get_mode("sess-gw") is None


def test_gateway_hook_only_captures_codex_messages():
    event = SimpleNamespace(get_command=lambda: "status", source=object())
    codex_cmd.on_pre_gateway_dispatch(event=event, gateway=FakeRunner("s"))
    assert codex_cmd._gateway_dispatch.get() is None


# ------------------------------------------------------------------ TUI ----
def test_tui_single_session_only(monkeypatch):
    import hermes_cli.plugins as plugins_mod

    monkeypatch.setattr(plugins_mod, "get_plugin_manager", lambda: SimpleNamespace(_cli_ref=None))
    sessions = {"sid1": {"agent": None, "session_key": "sess-tui"}}
    fake_server = SimpleNamespace(
        _sessions=sessions,
        _close_session_by_id=lambda sid, **_: sessions.pop(sid, None),
    )
    monkeypatch.setitem(sys.modules, "tui_gateway.server", fake_server)
    assert "已开启" in codex_cmd.codex_command("on")
    assert codex_cmd.get_mode("sess-tui") is not None

    sessions["sid2"] = {"agent": None, "session_key": "sess-other"}
    assert "多个会话" in codex_cmd.codex_command("status")


def test_session_finalize_drops_state(monkeypatch):
    agent = FakeAgent("sess-cli")
    _use_cli(monkeypatch, agent)
    codex_cmd.codex_command("on")
    _turn(agent)
    codex_cmd.on_session_finalize(session_id="sess-cli")
    assert codex_cmd.get_mode("sess-cli") is None
    assert agent._codex_session.closed
