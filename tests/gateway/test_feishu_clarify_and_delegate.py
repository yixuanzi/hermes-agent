"""Feishu clarify-card and foreground A2A delegate adapter coverage."""

import asyncio
import concurrent.futures
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)


def _ensure_feishu_mocks():
    if importlib.util.find_spec("lark_oapi") is None and "lark_oapi" not in sys.modules:
        mock = MagicMock()
        for name in ("lark_oapi", "lark_oapi.api.im.v1", "lark_oapi.event"):
            sys.modules.setdefault(name, mock)


_ensure_feishu_mocks()

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import DELEGATE_STEER_ACK, SendResult
from gateway.run import GatewayRunner
import plugins.platforms.feishu.adapter as feishu_module
from plugins.platforms.feishu.adapter import FeishuAdapter


def _make_adapter() -> FeishuAdapter:
    adapter = FeishuAdapter(PlatformConfig(enabled=True))
    adapter._client = MagicMock()
    return adapter


def _card_data(
    value,
    *,
    chat_id="oc_123",
    open_id="ou_owner",
    operator_user_id="",
    token="token-1",
):
    return SimpleNamespace(
        event=SimpleNamespace(
            token=token,
            context=SimpleNamespace(open_chat_id=chat_id),
            operator=SimpleNamespace(open_id=open_id, user_id=operator_user_id),
            action=SimpleNamespace(value=value),
        )
    )


class _Response:
    def __init__(self):
        self.card = None


class _Card:
    def __init__(self):
        self.type = None
        self.data = None


@pytest.mark.asyncio
async def test_feishu_open_ended_clarify_uses_base_text_fallback():
    adapter = _make_adapter()
    adapter.send = AsyncMock(return_value=SendResult(success=True, message_id="om_text"))

    result = await adapter.send_clarify(
        chat_id="oc_123", question="Explain?", choices=None, clarify_id="open", session_key="s"
    )

    assert result.success is True
    adapter.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_feishu_delegate_foreground_route_and_stream_are_isolated():
    adapter = _make_adapter()
    adapter.send = AsyncMock(return_value=SendResult(success=True, message_id="om_stream"))
    adapter.edit_message = AsyncMock(return_value=SendResult(success=True, message_id="om_stream"))

    runtime = adapter.build_delegate_foreground_runtime(
        channel_id="oc_123", thread_ts="th_1", user_id="ou_owner", chat_type="group"
    )
    first = runtime["input_factory"]()
    second = runtime["input_factory"]()
    assert first is not second
    first.enter_foreground()

    assert await adapter._maybe_route_delegate_foreground_message(
        text="follow up", chat_id="oc_123", thread_id="th_1", user_id="ou_owner", chat_type="group"
    )
    assert first.read_line(timeout=0) == "follow up"
    assert not await adapter._maybe_route_delegate_foreground_message(
        text="other user", chat_id="oc_123", thread_id="th_1", user_id="ou_other", chat_type="group"
    )

    route_key = adapter._delegate_route_key(
        chat_id="oc_123", thread_id="th_1", user_id="ou_owner", chat_type="group"
    )
    with patch.object(adapter, "_delegate_stream_edit_interval", return_value=60.0):
        await adapter.handle_delegate_ai_delta(route_key=route_key, chat_id="oc_123", content="hello", metadata={"thread_id": "th_1"})
        await adapter.handle_delegate_ai_delta(route_key=route_key, chat_id="oc_123", content=" world", metadata={"thread_id": "th_1"})
    adapter.send.assert_awaited_once()
    adapter.edit_message.assert_not_awaited()
    await adapter.handle_delegate_stream_segment_break(
        route_key=route_key, chat_id="oc_123", metadata={"thread_id": "th_1"}
    )
    adapter.edit_message.assert_awaited_once_with("oc_123", "om_stream", "hello world")
    assert route_key in adapter._delegate_stream_states
    first.close()
    assert route_key not in adapter._delegate_routes
    assert route_key not in adapter._delegate_stream_states


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


def _foreground_input(adapter):
    runtime = adapter.build_delegate_foreground_runtime(
        channel_id="oc_123", thread_ts="th_1", user_id="ou_owner", chat_type="group"
    )
    input_adapter = runtime["input_factory"]()
    input_adapter.enter_foreground()
    return input_adapter


async def _route(adapter, text):
    return await adapter._maybe_route_delegate_foreground_message(
        text=text, chat_id="oc_123", thread_id="th_1", user_id="ou_owner", chat_type="group"
    )


@pytest.mark.asyncio
async def test_feishu_delegate_input_steers_the_remote_turn_in_flight():
    adapter = _make_adapter()
    adapter.send = AsyncMock(return_value=SendResult(success=True, message_id="om_ack"))
    input_adapter = _foreground_input(adapter)
    session = _SteerSession(True)
    input_adapter.bind_a2a_session(session)

    assert await _route(adapter, "focus on X")

    assert session.steers == ["focus on X"]
    assert input_adapter.read_line(timeout=0) is None
    # The ack bypasses the card: as a body it would seal the delegate card mid-answer.
    adapter.send.assert_awaited_once_with(
        "oc_123", DELEGATE_STEER_ACK, metadata={"thread_id": "th_1", "hermes_card_bypass": True}
    )
    input_adapter.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [None, False])
async def test_feishu_delegate_input_queues_when_steer_is_unavailable_or_refused(outcome):
    adapter = _make_adapter()
    adapter.send = AsyncMock(return_value=SendResult(success=True, message_id="om_ack"))
    input_adapter = _foreground_input(adapter)
    input_adapter.bind_a2a_session(_SteerSession(outcome))

    assert await _route(adapter, "next question")

    assert input_adapter.read_line(timeout=0) == "next question"
    adapter.send.assert_not_awaited()
    input_adapter.close()


@pytest.mark.asyncio
async def test_feishu_delegate_loop_commands_are_never_steered():
    adapter = _make_adapter()
    adapter.send = AsyncMock(return_value=SendResult(success=True, message_id="om_ack"))
    input_adapter = _foreground_input(adapter)
    session = _SteerSession(True)
    input_adapter.bind_a2a_session(session)

    assert await _route(adapter, "/main")

    assert session.steers == []
    assert input_adapter.read_line(timeout=0) == "/main"
    input_adapter.unbind_a2a_session(session)
    assert input_adapter.schedule_steer("later") is None
    input_adapter.close()


@pytest.mark.parametrize(
    ("kind", "action_value", "state", "expected_value"),
    [
        (
            "approval",
            {
                "hermes_delegate_kind": "approval",
                "interaction_id": "approval-tenant-id",
                "choice": "once",
            },
            {"kind": "approval"},
            "once",
        ),
        (
            "clarify",
            {
                "hermes_delegate_kind": "clarify",
                "interaction_id": "clarify-tenant-id",
                "token": "0",
            },
            {"kind": "clarify", "choices": ["alpha"]},
            "alpha",
        ),
    ],
)
def test_feishu_delegate_card_accepts_tenant_user_id_from_callback(
    monkeypatch, kind, action_value, state, expected_value
):
    adapter = _make_adapter()
    adapter._loop = MagicMock()
    adapter._loop.is_closed.return_value = False
    adapter._allowed_group_users = {"ou_owner"}
    interaction_id = str(action_value["interaction_id"])
    responder = MagicMock(return_value=True)
    adapter._delegate_interactions[interaction_id] = {
        **state,
        "interaction_id": interaction_id,
        "chat_id": "oc_123",
        "thread_id": None,
        "user_id": "tenant-user-1",
        "responder": responder,
        "resolved": False,
        "selected": [],
    }
    monkeypatch.setattr(feishu_module, "P2CardActionTriggerResponse", _Response)
    monkeypatch.setattr(feishu_module, "CallBackCard", _Card)

    response = adapter._on_card_action_trigger(
        _card_data(
            action_value,
            operator_user_id="tenant-user-1",
            token=f"{kind}-tenant-user",
        )
    )

    assert response.card is not None
    assert adapter._delegate_interactions[interaction_id]["resolved"] is True
    responder.assert_called_once_with(interaction_id, kind, expected_value)


def test_gateway_binds_feishu_delegate_runtime_for_each_turn():
    adapter = _make_adapter()
    runner = object.__new__(GatewayRunner)
    runner.adapters = {Platform.FEISHU: adapter}
    agent = SimpleNamespace()
    source = SimpleNamespace(
        platform=Platform.FEISHU, chat_id="oc_123", thread_id=None, user_id="ou_owner", chat_type="dm"
    )

    from gateway.run_aegis_delegate import bind_delegate_foreground_runtime_for_turn

    bind_delegate_foreground_runtime_for_turn(runner, agent, source, event_message_id="om_1")

    assert agent._delegate_ext_output_adapter is not None
    assert callable(agent._delegate_ext_input_factory)


def _raw_send_recorder(adapter):
    """Capture which Feishu API (reply vs create) a send resolves to."""
    calls = []
    ok = SimpleNamespace(success=lambda: True, data=SimpleNamespace(message_id="om_card"))

    async def _run_blocking(fn, request):
        calls.append((fn, request))
        return ok

    adapter._run_blocking = _run_blocking
    adapter._build_reply_message_request = lambda message_id, body: ("reply", message_id)
    adapter._build_create_message_request = lambda id_type, body: ("create", id_type, body)
    adapter._build_reply_message_body = lambda **kw: kw
    adapter._build_create_message_body = lambda **kw: kw
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["approval", "clarify"])
async def test_feishu_delegate_card_posts_into_topic_for_omt_thread(kind):
    """A real topic id (omt_*) must never be sent to the reply API."""
    adapter = _make_adapter()
    calls = _raw_send_recorder(adapter)
    payload = {"interaction_id": f"{kind}-1", "command": "ls", "question": "Q?", "choices": ["a"]}
    sender = adapter.send_delegate_exec_approval if kind == "approval" else adapter.send_delegate_clarify
    result = await sender(chat_id="oc_group", payload=payload, responder=None, metadata={"thread_id": "omt_topic"})

    assert result.success is True
    assert len(calls) == 1
    fn, request = calls[0]
    assert fn is adapter._client.im.v1.message.create
    assert request[0] == "create" and request[1] == "thread_id"
    assert request[2]["receive_id"] == "omt_topic"
    assert f"{kind}-1" in adapter._delegate_interactions


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["approval", "clarify"])
async def test_feishu_delegate_card_still_replies_to_om_anchor(kind):
    """A message-id anchor (om_*, DM / auto-thread lane) keeps the reply path."""
    adapter = _make_adapter()
    calls = _raw_send_recorder(adapter)
    payload = {"interaction_id": f"{kind}-2", "command": "ls", "question": "Q?", "choices": ["a"]}
    sender = adapter.send_delegate_exec_approval if kind == "approval" else adapter.send_delegate_clarify
    result = await sender(chat_id="oc_dm", payload=payload, responder=None, metadata={"thread_id": "om_anchor"})

    assert result.success is True
    assert len(calls) == 1
    fn, request = calls[0]
    assert fn is adapter._client.im.v1.message.reply
    assert request == ("reply", "om_anchor")


@pytest.mark.asyncio
async def test_feishu_delegate_card_logs_when_not_delivered(caplog):
    adapter = _make_adapter()
    failed = SimpleNamespace(success=lambda: False, code=230001, msg="bad target", data=None)
    with patch.object(adapter, "_feishu_send_with_retry", new_callable=AsyncMock, return_value=failed):
        with caplog.at_level("WARNING"):
            result = await adapter.send_delegate_exec_approval(
                chat_id="oc_group", payload={"interaction_id": "ia-3", "command": "ls"},
                responder=None, metadata={"thread_id": "omt_topic"},
            )

    assert result.success is False
    assert "ia-3" not in adapter._delegate_interactions
    assert "delegate approval card not delivered" in caplog.text
