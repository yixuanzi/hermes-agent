from __future__ import annotations

import asyncio
import concurrent.futures
import importlib
import sys
import threading
import types
from collections import OrderedDict
from types import SimpleNamespace

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import DELEGATE_STEER_ACK, BasePlatformAdapter, SendResult
from gateway.session import SessionSource
from plugins.platforms.slack.adapter import SlackAdapter


class _RuntimeCaptureAdapter(BasePlatformAdapter):
    def __init__(self, *, platform: Platform):
        super().__init__(PlatformConfig(enabled=True, token="***"), platform)
        self.runtime_calls = []
        self._runtime_seq = 0

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None) -> SendResult:
        return SendResult(success=True, message_id="msg-1")

    async def edit_message(self, chat_id, message_id, content, **kwargs) -> SendResult:
        return SendResult(success=True, message_id=message_id)

    async def send_typing(self, chat_id, metadata=None) -> None:
        return None

    async def stop_typing(self, chat_id) -> None:
        return None

    async def get_chat_info(self, chat_id: str):
        return {"id": chat_id}

    def build_delegate_foreground_runtime(
        self,
        *,
        channel_id: str,
        thread_ts: str | None,
        user_id: str | None = None,
        chat_type: str | None = None,
    ):
        self._runtime_seq += 1
        marker = self._runtime_seq
        runtime = {
            "output": f"output-{marker}",
            "input_factory": lambda marker=marker: f"input-{marker}",
            "metadata": {"thread_id": thread_ts} if thread_ts else None,
        }
        self.runtime_calls.append(
            {
                "channel_id": channel_id,
                "thread_ts": thread_ts,
                "user_id": user_id,
                "chat_type": chat_type,
                "runtime": runtime,
            }
        )
        return runtime


class _FakeAgent:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.tools = []
        self.turn_snapshots = []
        type(self).instances.append(self)

    def run_conversation(self, message, conversation_history=None, task_id=None):
        del message, conversation_history, task_id
        input_factory = getattr(self, "_delegate_ext_input_factory", None)
        self.turn_snapshots.append(
            {
                "output": getattr(self, "_delegate_ext_output_adapter", None),
                "input_factory": input_factory,
                "input_value": input_factory() if callable(input_factory) else None,
            }
        )
        return {"final_response": "done", "messages": [], "api_calls": 1}


def _make_runner(adapter):
    gateway_run = importlib.import_module("gateway.run")
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.adapters = {adapter.platform: adapter}
    runner._voice_mode = {}
    runner._prefill_messages = []
    runner._ephemeral_system_prompt = ""
    runner._reasoning_config = None
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._session_db = None
    runner._running_agents = {}
    runner._session_run_generation = {}
    runner._agent_cache = OrderedDict()
    runner._agent_cache_lock = threading.Lock()
    runner.hooks = SimpleNamespace(loaded_hooks=False)
    runner.config = SimpleNamespace(
        thread_sessions_per_user=False,
        group_sessions_per_user=False,
        stt_enabled=False,
    )
    return runner


def _install_fake_agent(monkeypatch):
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = _FakeAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)


@pytest.fixture(autouse=True)
def _reset_fake_agent_instances():
    _FakeAgent.instances = []
    yield
    _FakeAgent.instances = []


@pytest.mark.asyncio
async def test_run_agent_injects_delegate_runtime_for_new_slack_agent(monkeypatch, tmp_path):
    _install_fake_agent(monkeypatch)
    adapter = _RuntimeCaptureAdapter(platform=Platform.SLACK)
    runner = _make_runner(adapter)
    gateway_run = importlib.import_module("gateway.run")
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "***"})

    source = SessionSource(
        platform=Platform.SLACK,
        chat_id="D123",
        chat_type="dm",
        thread_id="1717171717.500000",
    )

    result = await runner._run_agent(
        message="hello",
        context_prompt="",
        history=[],
        source=source,
        session_id="sess-slack-runtime-new",
        session_key="agent:main:slack:dm:D123:1717171717.500000",
        event_message_id="1717171717.500001",
    )

    assert result["final_response"] == "done"
    assert adapter.runtime_calls[0]["channel_id"] == "D123"
    assert adapter.runtime_calls[0]["thread_ts"] == "1717171717.500000"
    assert adapter.runtime_calls[0]["chat_type"] == "dm"
    assert _FakeAgent.instances[0].turn_snapshots[0]["output"] == "output-1"
    assert _FakeAgent.instances[0].turn_snapshots[0]["input_value"] == "input-1"


@pytest.mark.asyncio
async def test_run_agent_refreshes_delegate_runtime_for_cached_slack_agent(monkeypatch, tmp_path):
    _install_fake_agent(monkeypatch)
    adapter = _RuntimeCaptureAdapter(platform=Platform.SLACK)
    runner = _make_runner(adapter)
    gateway_run = importlib.import_module("gateway.run")
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "***"})

    source = SessionSource(platform=Platform.SLACK, chat_id="D123", chat_type="dm", user_id="U_DM")

    await runner._run_agent(
        message="hello",
        context_prompt="",
        history=[],
        source=source,
        session_id="sess-slack-runtime-cached",
        session_key="agent:main:slack:dm:D123",
        event_message_id="1717171717.600001",
    )
    await runner._run_agent(
        message="hello again",
        context_prompt="",
        history=[],
        source=source,
        session_id="sess-slack-runtime-cached",
        session_key="agent:main:slack:dm:D123",
        event_message_id="1717171717.600002",
    )

    assert len(_FakeAgent.instances) == 1
    assert [call["thread_ts"] for call in adapter.runtime_calls] == [
        "1717171717.600001",
        "1717171717.600002",
    ]
    assert [snapshot["output"] for snapshot in _FakeAgent.instances[0].turn_snapshots] == [
        "output-1",
        "output-2",
    ]


@pytest.mark.asyncio
async def test_slack_delegate_input_factory_returns_fresh_foreground_adapter():
    adapter = SlackAdapter(PlatformConfig(enabled=True, token="xoxb-test"))
    runtime = adapter.build_delegate_foreground_runtime(
        channel_id="D123",
        thread_ts="1717171717.700000",
        user_id="U123",
        chat_type="dm",
    )

    first = runtime["input_factory"]()
    second = runtime["input_factory"]()

    assert first is not second
    assert first.enter_foreground() is True
    assert await adapter._maybe_route_delegate_foreground_message(
        text="follow up",
        channel_id="D123",
        thread_ts="1717171717.700000",
        user_id="U123",
        chat_type="dm",
    )
    assert first.read_line(timeout=0.01) == "follow up"
    assert await adapter._maybe_route_delegate_foreground_message(
        text="miss",
        channel_id="D999",
        thread_ts="1717171717.700000",
        user_id="U123",
        chat_type="dm",
    ) is False
    first.exit_foreground()


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["/main", "/exit"])
async def test_slack_delegate_foreground_routes_mentioned_exit_commands_without_thread_context(
    monkeypatch,
    command,
):
    adapter = SlackAdapter(PlatformConfig(enabled=True, token="xoxb-test"))
    adapter._bot_user_id = "B123"
    adapter._team_bot_user_ids["T123"] = "B123"
    runtime = adapter.build_delegate_foreground_runtime(
        channel_id="C123",
        thread_ts="1717171717.710000",
        user_id="U123",
        chat_type="group",
    )
    input_adapter = runtime["input_factory"]()
    assert input_adapter.enter_foreground() is True

    async def fail_handle_message(event):
        raise AssertionError(f"delegate foreground message leaked to main session: {event.text}")

    monkeypatch.setattr(adapter, "handle_message", fail_handle_message)
    async def fake_resolve_user_name(*args, **kwargs):
        del args, kwargs
        return "Ada"

    monkeypatch.setattr(adapter, "_resolve_user_name", fake_resolve_user_name)
    monkeypatch.setattr(adapter, "_has_active_session_for_thread", lambda **kwargs: False)

    async def fake_fetch_thread_context(**kwargs):
        del kwargs
        return "[thread context]\n"

    monkeypatch.setattr(adapter, "_fetch_thread_context", fake_fetch_thread_context)

    await adapter._handle_slack_message(
        {
            "type": "message",
            "channel": "C123",
            "channel_type": "channel",
            "team": "T123",
            "user": "U123",
            "text": f"<@B123> {command}",
            "thread_ts": "1717171717.710000",
            "ts": f"1717171717.71000{1 if command == '/main' else 2}",
        }
    )

    assert input_adapter.read_line(timeout=0.01) == command
    input_adapter.exit_foreground()


@pytest.mark.asyncio
async def test_slack_delegate_foreground_lets_gateway_commands_through(monkeypatch):
    adapter = SlackAdapter(PlatformConfig(enabled=True, token="xoxb-test"))
    adapter._bot_user_id = "B123"
    adapter._team_bot_user_ids["T123"] = "B123"
    runtime = adapter.build_delegate_foreground_runtime(
        channel_id="C123",
        thread_ts="1717171717.720000",
        user_id="U123",
        chat_type="group",
    )
    input_adapter = runtime["input_factory"]()
    assert input_adapter.enter_foreground() is True
    dispatched = []

    async def capture_handle_message(event):
        dispatched.append(event)

    monkeypatch.setattr(adapter, "handle_message", capture_handle_message)

    async def fake_resolve_user_name(*args, **kwargs):
        del args, kwargs
        return "Ada"

    monkeypatch.setattr(adapter, "_resolve_user_name", fake_resolve_user_name)
    monkeypatch.setattr(adapter, "_has_active_session_for_thread", lambda **kwargs: False)

    async def fake_fetch_thread_context(**kwargs):
        del kwargs
        return ""

    monkeypatch.setattr(adapter, "_fetch_thread_context", fake_fetch_thread_context)

    await adapter._handle_slack_message(
        {
            "type": "message",
            "channel": "C123",
            "channel_type": "channel",
            "team": "T123",
            "user": "U123",
            "text": "<@B123> /stop",
            "thread_ts": "1717171717.720000",
            "ts": "1717171717.720001",
        }
    )

    assert input_adapter.read_line(timeout=0.01) is None
    assert [event.get_command() for event in dispatched] == ["stop"]
    input_adapter.exit_foreground()


@pytest.mark.asyncio
async def test_slack_delegate_foreground_keeps_thread_context_for_regular_input(monkeypatch):
    adapter = SlackAdapter(PlatformConfig(enabled=True, token="xoxb-test"))
    adapter._bot_user_id = "B123"
    adapter._team_bot_user_ids["T123"] = "B123"
    runtime = adapter.build_delegate_foreground_runtime(
        channel_id="C123",
        thread_ts="1717171717.720000",
        user_id="U123",
        chat_type="group",
    )
    input_adapter = runtime["input_factory"]()
    assert input_adapter.enter_foreground() is True

    async def fail_handle_message(event):
        raise AssertionError(f"delegate foreground message leaked to main session: {event.text}")

    monkeypatch.setattr(adapter, "handle_message", fail_handle_message)
    async def fake_resolve_user_name(*args, **kwargs):
        del args, kwargs
        return "Ada"

    monkeypatch.setattr(adapter, "_resolve_user_name", fake_resolve_user_name)
    monkeypatch.setattr(adapter, "_has_active_session_for_thread", lambda **kwargs: False)

    async def fake_fetch_thread_context(**kwargs):
        del kwargs
        return "[thread context]\n"

    monkeypatch.setattr(adapter, "_fetch_thread_context", fake_fetch_thread_context)

    await adapter._handle_slack_message(
        {
            "type": "message",
            "channel": "C123",
            "channel_type": "channel",
            "team": "T123",
            "user": "U123",
            "text": "<@B123> continue",
            "thread_ts": "1717171717.720000",
            "ts": "1717171717.720001",
        }
    )

    assert input_adapter.read_line(timeout=0.01) == "[thread context]\ncontinue"
    input_adapter.exit_foreground()


@pytest.mark.asyncio
async def test_slack_delegate_output_flushes_ai_before_tool_call(monkeypatch):
    adapter = SlackAdapter(PlatformConfig(enabled=True, token="xoxb-test"))
    sent = []
    edited = []

    async def fake_send(chat_id, content, reply_to=None, metadata=None):
        del reply_to
        sent.append((chat_id, content, metadata))
        return SendResult(success=True, message_id=f"msg-{len(sent)}")

    async def fake_edit(chat_id, message_id, content, **kwargs):
        del kwargs
        edited.append((chat_id, message_id, content))
        return SendResult(success=True, message_id=message_id)

    monkeypatch.setattr(adapter, "send", fake_send)
    monkeypatch.setattr(adapter, "edit_message", fake_edit)
    runtime = adapter.build_delegate_foreground_runtime(
        channel_id="C123",
        thread_ts="1717171717.800000",
        user_id="U123",
        chat_type="group",
    )
    output = runtime["output"]

    output.emit("delegate", "ai_delta", "hello", session_id="ctx-1")
    await asyncio.sleep(0)
    output.emit("delegate", "ai_delta", " world", session_id="ctx-1")
    await asyncio.sleep(0)
    output.emit("delegate", "ai", "hello world", session_id="ctx-1")
    await asyncio.sleep(0)
    output.emit("delegate", "tool_call", 'terminal {"command":"pwd"}', session_id="ctx-1")
    await asyncio.sleep(0)

    assert sent[0] == ("C123", "hello", {"thread_id": "1717171717.800000"})
    assert edited == [("C123", "msg-1", "hello world")]
    assert len(sent) == 2
    assert "terminal" in sent[1][1]
    assert "pwd" in sent[1][1]


@pytest.mark.asyncio
async def test_slack_delegate_output_splits_long_delta(monkeypatch):
    adapter = SlackAdapter(PlatformConfig(enabled=True, token="xoxb-test"))
    adapter.MAX_MESSAGE_LENGTH = 12
    sent = []

    async def fake_send(chat_id, content, reply_to=None, metadata=None):
        del reply_to
        sent.append((chat_id, content, metadata))
        return SendResult(success=True, message_id=f"msg-{len(sent)}")

    monkeypatch.setattr(adapter, "send", fake_send)
    runtime = adapter.build_delegate_foreground_runtime(
        channel_id="C123",
        thread_ts="1717171717.900000",
        user_id="U123",
        chat_type="group",
    )

    runtime["output"].emit("delegate", "ai_delta", "abcdefghijklmnopqrstuvwxyz", session_id="ctx-1")
    await asyncio.sleep(0)

    assert len(sent) > 1
    assert "".join(item[1] for item in sent) == "abcdefghijklmnopqrstuvwxyz"


@pytest.mark.asyncio
async def test_slack_delegate_delta_edits_same_message_at_most_once_per_three_seconds(monkeypatch):
    adapter = SlackAdapter(PlatformConfig(enabled=True, token="xoxb-test"))
    sent = []
    edited = []
    now = {"value": 100.0}

    async def fake_send(chat_id, content, reply_to=None, metadata=None):
        del reply_to
        sent.append((chat_id, content, metadata))
        return SendResult(success=True, message_id="msg-1")

    async def fake_edit(chat_id, message_id, content, **kwargs):
        del kwargs
        edited.append((chat_id, message_id, content))
        return SendResult(success=True, message_id=message_id)

    monkeypatch.setattr(adapter, "send", fake_send)
    monkeypatch.setattr(adapter, "edit_message", fake_edit)
    monkeypatch.setattr("plugins.platforms.slack.adapter.time.monotonic", lambda: now["value"])

    route_key = adapter._delegate_route_key(
        channel_id="C123",
        thread_ts="1717171717.910000",
        user_id="U123",
        chat_type="group",
    )
    metadata = {"thread_id": "1717171717.910000"}

    await adapter.handle_delegate_ai_delta(
        route_key=route_key,
        chat_id="C123",
        content="hello",
        metadata=metadata,
    )
    await adapter.handle_delegate_ai_delta(
        route_key=route_key,
        chat_id="C123",
        content=" world",
        metadata=metadata,
    )

    assert sent == [("C123", "hello", metadata)]
    assert edited == []

    now["value"] = 103.1
    await adapter.handle_delegate_ai_delta(
        route_key=route_key,
        chat_id="C123",
        content="!",
        metadata=metadata,
    )

    assert edited == [("C123", "msg-1", "hello world!")]


class _SteerSession:
    def __init__(self, outcome):
        self.outcome = outcome
        self.steers = []

    def schedule_steer(self, text):
        self.steers.append(text)
        if self.outcome is None:
            return None
        future = concurrent.futures.Future()
        future.set_result(self.outcome)
        return future


def _slack_foreground_input(adapter):
    runtime = adapter.build_delegate_foreground_runtime(
        channel_id="C123", thread_ts="1717171717.730000", user_id="U123", chat_type="group"
    )
    input_adapter = runtime["input_factory"]()
    assert input_adapter.enter_foreground() is True
    return input_adapter


async def _route_slack(adapter, text):
    return await adapter._maybe_route_delegate_foreground_message(
        text=text,
        channel_id="C123",
        thread_ts="1717171717.730000",
        user_id="U123",
        chat_type="group",
    )


def _recording_send(adapter):
    sent = []

    async def _send(chat_id, content, metadata=None, **kwargs):
        del kwargs
        sent.append((chat_id, content, metadata))
        return SendResult(success=True, message_id="ack")

    adapter.send = _send
    return sent


@pytest.mark.asyncio
async def test_slack_delegate_input_steers_the_remote_turn_in_flight():
    adapter = SlackAdapter(PlatformConfig(enabled=True, token="xoxb-test"))
    sent = _recording_send(adapter)
    input_adapter = _slack_foreground_input(adapter)
    session = _SteerSession(True)
    input_adapter.bind_a2a_session(session)

    assert await _route_slack(adapter, "focus on X")

    assert session.steers == ["focus on X"]
    assert input_adapter.read_line(timeout=0) is None
    assert sent == [("C123", DELEGATE_STEER_ACK, {"thread_id": "1717171717.730000"})]
    input_adapter.exit_foreground()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [None, False])
async def test_slack_delegate_input_queues_when_steer_is_unavailable_or_refused(outcome):
    adapter = SlackAdapter(PlatformConfig(enabled=True, token="xoxb-test"))
    sent = _recording_send(adapter)
    input_adapter = _slack_foreground_input(adapter)
    input_adapter.bind_a2a_session(_SteerSession(outcome))

    assert await _route_slack(adapter, "next question")

    assert input_adapter.read_line(timeout=0) == "next question"
    assert sent == []
    input_adapter.exit_foreground()


@pytest.mark.asyncio
async def test_slack_delegate_loop_commands_are_never_steered():
    adapter = SlackAdapter(PlatformConfig(enabled=True, token="xoxb-test"))
    _recording_send(adapter)
    input_adapter = _slack_foreground_input(adapter)
    session = _SteerSession(True)
    input_adapter.bind_a2a_session(session)

    assert await _route_slack(adapter, "/main")

    assert session.steers == []
    assert input_adapter.read_line(timeout=0) == "/main"
    input_adapter.unbind_a2a_session(session)
    assert input_adapter.schedule_steer("later") is None
    input_adapter.exit_foreground()
