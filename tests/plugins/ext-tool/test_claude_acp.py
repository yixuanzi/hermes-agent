"""ext-tools /claude runtime：ClaudeAcpSession 与 ACP 子进程（fake claude-agent-acp）的整回合交互。"""

import json
import shlex
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "plugins" / "ext-tools"))

import claude_acp
from claude_acp import ClaudeAcpSession, ClaudeEventBridge

FAKE_AGENT = Path(__file__).with_name("fake_claude_acp_agent.py")


@pytest.fixture(autouse=True)
def fake_acp(monkeypatch, tmp_path):
    log = tmp_path / "acp.jsonl"
    monkeypatch.setenv(claude_acp.ACP_COMMAND_ENV, f"{shlex.quote(sys.executable)} {shlex.quote(str(FAKE_AGENT))}")
    monkeypatch.setenv("CLAUDE_CODE_EXECUTABLE", sys.executable)
    monkeypatch.setenv("FAKE_ACP_LOG", str(log))
    return log


def _sent(log: Path, method: str) -> list[dict]:
    if not log.exists():
        return []
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return [row for row in rows if row.get("method") == method]


def _responses(log: Path) -> list[dict]:
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return [row for row in rows if "method" not in row]


class FakeAgent:
    def __init__(self):
        self.deltas, self.reasoning, self.interim, self.tools, self.cards = [], [], [], [], []
        self.activity = []
        self._stream_needs_break = False
        self.show_commentary = True

    def _fire_stream_delta(self, text):
        self.deltas.append(text)

    def _fire_reasoning_delta(self, text):
        self.reasoning.append(text)

    def _emit_interim_assistant_message(self, message):
        self.interim.append(message["content"])

    def _reset_stream_delivery_tracking(self):
        pass

    def _touch_activity(self, desc):
        self.activity.append(desc)

    def tool_progress_callback(self, event, name, preview, args, **kwargs):
        self.tools.append((event, name, preview, args, kwargs.get("is_error"), kwargs.get("result")))

    def tool_start_callback(self, call_id, name, args):
        self.cards.append(("start", call_id, name, args))

    def tool_complete_callback(self, call_id, name, args, result):
        self.cards.append(("complete", call_id, name, args, result))


def _session(tmp_path, **kwargs) -> ClaudeAcpSession:
    return ClaudeAcpSession(cwd=str(tmp_path), **kwargs)


def test_tool_turn_streams_projects_and_asks_hermes_approval(tmp_path, fake_acp):
    agent = FakeAgent()
    approvals = []

    def approve(command, description, *, allow_permanent):
        approvals.append((command, description, allow_permanent))
        return "once"

    session = _session(tmp_path, on_event=ClaudeEventBridge(agent), approval_callback=approve)
    try:
        turn = session.run_turn("tool")
    finally:
        session.close()

    assert turn.error is None and not turn.interrupted and not turn.should_retire
    assert turn.final_text == "All done."
    assert turn.thread_id == "new-session"
    assert turn.tool_iterations == 1
    # reasoning 默认不回灌（bridge.claude_project_reasoning: false）
    assert turn.projected_messages == [
        {"role": "assistant", "content": "Checking."},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "claude_t1", "type": "function",
            "function": {"name": "Bash", "arguments": '{"command": "echo hi"}'},
        }]},
        {"role": "tool", "tool_call_id": "claude_t1", "content": "hi"},
        {"role": "assistant", "content": "All done."},
    ]
    # 输入桶是本回合最后一个 usage_update.used（当前上下文），totalTokens 是本回合累计
    assert turn.token_usage_last == {
        "inputTokens": 1234, "cachedInputTokens": 0, "outputTokens": 5,
        "reasoningOutputTokens": 0, "totalTokens": 135,
    }
    assert turn.model_context_window == 200000

    assert approvals[0][0] == "echo hi" and approvals[0][2] is True
    assert "don't ask again for echo" in approvals[0][1]
    assert _responses(fake_acp)[-1]["result"] == {"outcome": {"outcome": "selected", "optionId": "allow-once"}}

    # Claude Code 自己执行工具：不声明 fs / terminal 能力
    init = _sent(fake_acp, "initialize")[0]["params"]
    assert init["clientCapabilities"] == {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}

    assert agent.deltas == ["Checking.", "All ", "done."]
    assert agent.reasoning == ["plan it"]
    assert agent.interim == ["Checking."]  # 工具前的消息是过程说明；最终回答不走 interim
    assert [t[:2] for t in agent.tools] == [("tool.started", "Bash"), ("tool.completed", "Bash")]
    assert agent.tools[0][2:4] == ("echo hi", {"command": "echo hi"})
    assert agent.tools[1][4:] == (False, "hi")
    assert agent.cards[0] == ("start", "claude_t1", "Bash", {"command": "echo hi"})
    assert agent.cards[1][:2] == ("complete", "claude_t1")
    assert agent.activity  # 长回合不被 gateway 空闲看门狗误判


def test_permission_fails_closed_without_approval_ui(tmp_path, fake_acp):
    agent = FakeAgent()
    session = _session(tmp_path, on_event=ClaudeEventBridge(agent))
    try:
        turn = session.run_turn("tool")
    finally:
        session.close()
    assert _responses(fake_acp)[-1]["result"]["outcome"]["optionId"] == "reject"
    assert turn.projected_messages[2] == {
        "role": "tool", "tool_call_id": "claude_t1", "content": "[error] denied by user",
    }
    assert agent.tools[-1][4] is True


@pytest.mark.parametrize(("kwargs", "option"), [
    ({"auto_approve": True}, "allow-once"),
    ({"approval_callback": lambda *a, **k: "always"}, "allow-with-updates"),
    ({"approval_callback": lambda *a, **k: "session"}, "allow-once"),
    ({"approval_callback": lambda *a, **k: "deny"}, "reject"),
])
def test_permission_choice_mapping(tmp_path, fake_acp, kwargs, option):
    session = _session(tmp_path, **kwargs)
    try:
        session.run_turn("tool")
    finally:
        session.close()
    assert _responses(fake_acp)[-1]["result"]["outcome"]["optionId"] == option


def test_permission_mode_applied_and_restored(tmp_path, fake_acp):
    session = _session(tmp_path, permission_mode="plan")
    try:
        session.ensure_started()
        assert session.info["mode"] == "plan" and session.info["default_mode"] == "default"
        assert session.run_turn("hi").final_text == "mode=plan echo=hi"
        session.set_permission_mode(None)  # 未指定时恢复 Claude Code 的初始模式
        assert session.run_turn("hi").final_text == "mode=default echo=hi"
        assert session.info["model"] == "sonnet" and session.info["model_name"] == "Sonnet 5.5"
        assert session.info["account"]["label"] == "Claude Team"
    finally:
        session.close()
    assert [m["params"]["modeId"] for m in _sent(fake_acp, "session/set_mode")] == ["plan", "default"]


def test_unknown_permission_mode_fails_the_turn(tmp_path):
    session = _session(tmp_path, permission_mode="dontAsk")
    turn = session.run_turn("hi")
    session.close()
    assert "不接受权限模式" in turn.error and turn.should_retire


def test_interrupt_sends_session_cancel(tmp_path, fake_acp):
    session = _session(tmp_path)
    threading.Timer(0.5, session.request_interrupt).start()
    try:
        turn = session.run_turn("slow")
    finally:
        session.close()
    assert turn.interrupted and turn.error is None
    assert _sent(fake_acp, "session/cancel")


def test_steer_injects_into_running_turn(tmp_path, fake_acp):
    session = _session(tmp_path)
    accepted = []
    threading.Timer(0.5, lambda: accepted.append(session.request_steer("go left"))).start()
    try:
        turn = session.run_turn("slow")
    finally:
        session.close()
    assert accepted == [True]
    assert turn.final_text == "Working steered:go left"
    assert {"role": "user", "content": "go left"} in turn.projected_messages
    assert not session.request_steer("idle")  # 没有运行中的回合时由 core 排队到下一轮


def test_resume_continues_previous_claude_session(tmp_path, fake_acp):
    session = _session(tmp_path, resume_session_id="known-session")
    try:
        assert session.ensure_started() == "known-session"
        assert session.info["resumed"]
    finally:
        session.close()
    gone = _session(tmp_path, resume_session_id="gone")
    try:
        assert gone.ensure_started() == "new-session"  # 续接失败时新开会话
    finally:
        gone.close()


def test_logged_out_claude_code_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_ACP_AUTH", "none")
    session = _session(tmp_path)
    turn = session.run_turn("hi")
    session.close()
    assert "/login" in turn.error and turn.should_retire


@pytest.mark.parametrize(("prompt", "fragment"), [("crash", "进程"), ("error", "query closed")])
def test_dead_or_failed_turn_retires_session(tmp_path, prompt, fragment):
    session = _session(tmp_path)
    turn = session.run_turn(prompt)
    session.close()
    assert fragment in turn.error and turn.should_retire


def test_core_compress_entry_runs_claude_compact(tmp_path, fake_acp):
    session = _session(tmp_path)
    try:
        result = session.compact_thread()
    finally:
        session.close()
    assert result.compacted and result.error is None
    assert _sent(fake_acp, "session/prompt")[-1]["params"]["prompt"] == [{"type": "text", "text": "/compact"}]


def test_compact_runs_claude_compact(tmp_path, fake_acp):
    session = _session(tmp_path)
    try:
        result = session.compact()
    finally:
        session.close()
    assert result.compacted and result.error is None
    assert result.projected_messages == [] and result.token_usage_last is None
    assert _sent(fake_acp, "session/prompt")[-1]["params"]["prompt"] == [{"type": "text", "text": "/compact"}]


def test_close_ends_the_adapter_process(tmp_path):
    session = _session(tmp_path)
    session.ensure_started()
    conn = session._conn
    assert conn.is_alive() and session.started
    session.close()
    assert session.closed and not session.started and not conn.is_alive()
    assert session.acp_session_id == "new-session"  # 供替换会话 resume


def test_tool_record_off_keeps_final_reply_and_steer(tmp_path, monkeypatch):
    import tool_record

    monkeypatch.delenv(tool_record.ENV_VAR, raising=False)
    session = _session(tmp_path)
    tool_record.install(session)
    try:
        tool_turn = session.run_turn("tool")
        threading.Timer(0.5, lambda: session.request_steer("go left")).start()
        steered = session.run_turn("slow")
    finally:
        session.close()
    # 工具调用、工具结果与中间说明都不进 Hermes 会话
    assert tool_turn.projected_messages == [{"role": "assistant", "content": "All done."}]
    assert steered.projected_messages == [
        {"role": "user", "content": "go left"},
        {"role": "assistant", "content": "Working steered:go left"},
    ]


def test_turn_without_usage_update_reports_no_usage(tmp_path):
    session = ClaudeAcpSession(cwd=str(tmp_path))
    try:
        turn = session.run_turn("hello")
        # 回合之间的 usage_update 只更新状态，不算进下一回合
        session._absorb_update({"sessionUpdate": "usage_update", "used": 999, "size": 200000}, None)
        assert session.info["context_used"] == 999
        later = session.run_turn("again")
    finally:
        session.close()
    assert turn.error is None and turn.token_usage_last is None
    assert later.error is None and later.token_usage_last is None


def test_context_is_last_request_not_turn_sum(monkeypatch):
    """多步工具回合：PromptResponse.usage 累加了每次请求重读的缓存，不能当上下文。"""
    from types import SimpleNamespace

    from agent import usage_pricing
    from agent.codex_runtime import _record_codex_app_server_usage
    from agent.transports.codex_app_server_session import TurnResult

    monkeypatch.setattr(
        usage_pricing, "estimate_usage_cost",
        lambda *a, **k: SimpleNamespace(amount_usd=None, status="unknown", source="none"),
    )
    # 30 次请求，每次重读约 22 万缓存上下文
    turn_sum = {"inputTokens": 3_000, "outputTokens": 40_000, "cachedReadTokens": 6_600_000,
                "cachedWriteTokens": 60_000, "totalTokens": 6_703_000}
    turn = TurnResult()
    turn.token_usage_last = claude_acp._token_usage(turn_sum, 231_500)
    turn.model_context_window = 1_000_000

    class Compressor:
        last_prompt_tokens = 0
        context_length = 0

        def update_from_response(self, usage):
            self.last_prompt_tokens = usage["prompt_tokens"]

    agent = SimpleNamespace(
        context_compressor=Compressor(), session_api_calls=0, session_prompt_tokens=0,
        session_completion_tokens=0, session_total_tokens=0, session_input_tokens=0,
        session_output_tokens=0, session_cache_read_tokens=0, session_cache_write_tokens=0,
        session_reasoning_tokens=0, session_estimated_cost_usd=0.0, _session_db=None,
        session_id="", model="m", provider="custom", base_url="", api_key="",
    )
    recorded = _record_codex_app_server_usage(agent, turn)

    assert agent.context_compressor.last_prompt_tokens == 231_500
    assert recorded["last_prompt_tokens"] == 231_500
    assert agent.context_compressor.context_length == 1_000_000
    assert agent.session_total_tokens == 6_703_000  # 本回合累计仍保留在 totalTokens
    # 压缩后的 0 占位或没有 usage_update：不报用量
    assert claude_acp._token_usage(turn_sum, 0) is None
    assert claude_acp._token_usage(turn_sum, None) is None


def test_image_parts_become_acp_image_blocks():
    blocks = claude_acp.acp_prompt([
        {"type": "text", "text": "what is this"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        {"type": "image_url", "image_url": {"url": "https://example.invalid/x.png"}},
    ])
    assert blocks == [
        {"type": "text", "text": "what is this"},
        {"type": "image", "mimeType": "image/png", "data": "AAAA"},
        {"type": "text", "text": "[image attached]"},
    ]
    assert claude_acp.acp_prompt("hello") == [{"type": "text", "text": "hello"}]


def test_spawn_env_drops_hermes_provider_keys(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-hermes")
    env = claude_acp._spawn_env("/usr/local/bin/claude")
    assert "ANTHROPIC_API_KEY" not in env
    assert env["CLAUDE_CODE_EXECUTABLE"] == "/usr/local/bin/claude"


def test_acp_command_resolution(monkeypatch):
    monkeypatch.delenv(claude_acp.ACP_COMMAND_ENV, raising=False)
    monkeypatch.setattr(claude_acp.shutil, "which", lambda name: "/bin/npx" if name == "npx" else None)
    assert claude_acp.resolve_acp_command() == ["/bin/npx", "-y", claude_acp.DEFAULT_ACP_PACKAGE]
    monkeypatch.setattr(claude_acp.shutil, "which", lambda name: None)
    with pytest.raises(claude_acp.ClaudeAcpError):
        claude_acp.resolve_acp_command()
