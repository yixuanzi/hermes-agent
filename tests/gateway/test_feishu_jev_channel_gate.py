"""Jev channel-admission gate on the Feishu adapter.

Covers the path an unmentioned group message takes: ``_admit`` now reports it
separately from a policy rejection, the gate decides whether it is ours to
answer, and an admitted message is answered in a topic under itself.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from tests.gateway.feishu_helpers import (
    make_adapter_skeleton,
    make_message,
    make_sender,
    stub_mention,
)


# --- _admit reports the mention miss separately -----------------------------


def test_an_allowed_sender_who_did_not_mention_us_is_reported_as_a_mention_miss():
    adapter = make_adapter_skeleton(require_mention=True, group_policy="open")
    stub_mention(adapter, False)
    reason = adapter._admit(make_sender(), make_message(chat_type="group"))
    assert reason == "group_mention_missing"


def test_a_sender_the_group_policy_rejects_stays_a_policy_rejection():
    adapter = make_adapter_skeleton(require_mention=True, group_policy="allowlist")
    stub_mention(adapter, False)
    reason = adapter._admit(make_sender(), make_message(chat_type="group"))
    assert reason == "group_policy_rejected"


def test_a_mentioned_group_message_is_admitted_outright():
    adapter = make_adapter_skeleton(require_mention=True, group_policy="open")
    stub_mention(adapter, True)
    assert adapter._admit(make_sender(), make_message(chat_type="group")) is None


def test_a_dm_never_reaches_the_mention_gate():
    adapter = make_adapter_skeleton(require_mention=True, group_policy="open")
    stub_mention(adapter, False)
    assert adapter._admit(make_sender(), make_message(chat_type="p2p")) is None


# --- the adapter's Jev helpers ---------------------------------------------


def _adapter_with_jev(monkeypatch, *, active: bool, verdict):
    from plugins.platforms.feishu import adapter as feishu_adapter
    from agent import jev_policy

    adapter = make_adapter_skeleton()
    monkeypatch.setattr(jev_policy, "load_jev_settings", lambda: SimpleNamespace(name="stub"))
    monkeypatch.setattr(jev_policy, "channel_autoreply_active", lambda _s: active)

    async def _judge(text, **_kwargs):
        return verdict

    monkeypatch.setattr(jev_policy, "judge_channel_relevance_async", _judge)
    return adapter, feishu_adapter


def test_the_gate_is_reported_off_when_autoreply_is_not_active(monkeypatch):
    adapter, _ = _adapter_with_jev(monkeypatch, active=False, verdict=True)
    assert adapter._jev_channel_autoreply_enabled() is False


def test_the_gate_is_reported_on_when_autoreply_is_active(monkeypatch):
    adapter, _ = _adapter_with_jev(monkeypatch, active=True, verdict=True)
    assert adapter._jev_channel_autoreply_enabled() is True


@pytest.mark.parametrize(
    "verdict, expected", [(True, True), (False, False), (None, False)],
)
def test_only_an_explicit_yes_admits_the_message(monkeypatch, verdict, expected):
    adapter, _ = _adapter_with_jev(monkeypatch, active=True, verdict=verdict)
    result = asyncio.run(
        adapter._jev_message_in_scope(text="…", chat_name="ops", sender_name="zhang")
    )
    assert result is expected


def test_a_raising_classifier_keeps_the_bot_quiet(monkeypatch):
    from agent import jev_policy

    adapter = make_adapter_skeleton()
    monkeypatch.setattr(jev_policy, "load_jev_settings", lambda: SimpleNamespace())

    async def _boom(text, **_kwargs):
        raise RuntimeError("classifier exploded")

    monkeypatch.setattr(jev_policy, "judge_channel_relevance_async", _boom)
    result = asyncio.run(
        adapter._jev_message_in_scope(text="…", chat_name="ops", sender_name="zhang")
    )
    assert result is False


def test_an_unimportable_policy_module_keeps_the_bot_quiet(monkeypatch):
    adapter = make_adapter_skeleton()
    monkeypatch.setattr(type(adapter), "_jev_settings", lambda self: None)
    assert adapter._jev_channel_autoreply_enabled() is False
    assert (
        asyncio.run(adapter._jev_message_in_scope(text="…", chat_name="", sender_name=""))
        is False
    )


# --- the admission hand-off ------------------------------------------------


def _handoff_adapter(
    monkeypatch, *, autoreply_on: bool, require_mention: bool = True, mentioned: bool = False,
):
    """An adapter whose _process_inbound_message only records its kwargs."""
    from tests.gateway.feishu_helpers import install_dedup_state

    adapter = make_adapter_skeleton(
        require_mention=require_mention, group_policy="open", allow_bots="all",
    )
    install_dedup_state(adapter)
    stub_mention(adapter, mentioned)
    monkeypatch.setattr(
        type(adapter), "_jev_channel_autoreply_enabled", lambda self: autoreply_on
    )
    seen: list[dict] = []

    async def _process(**kwargs):
        seen.append(kwargs)

    adapter._process_inbound_message = _process
    return adapter, seen


def _event(*, sender_type="user", chat_type="group", message_id="om_1"):
    return SimpleNamespace(
        event=SimpleNamespace(
            sender=make_sender(sender_type=sender_type),
            message=make_message(message_id=message_id, chat_type=chat_type),
        )
    )


# The full admission matrix for a human group message: require_mention x
# HERMES_JEV_CHANNEL_AUTOREPLY x whether the bot was @-mentioned.
#
# The row that matters is require_mention=False + autoreply=True + no mention.
# Tying the gate to the drop path alone left that combination unguarded — and
# require_mention=False is precisely the configuration where the bot would
# otherwise answer EVERY message in the group, so it is the one that most needs
# a business-scope filter. Every autoreply=False row must keep the exact
# behavior it had before the gate existed.
_ADMISSION_MATRIX = [
    # require_mention, autoreply, mentioned, expected outcome
    pytest.param(True,  False, False, "dropped",  id="mention_required:off:no_mention_dropped"),
    pytest.param(True,  False, True,  "normal",   id="mention_required:off:mentioned_normal"),
    pytest.param(True,  True,  False, "judged",   id="mention_required:on:no_mention_judged"),
    pytest.param(True,  True,  True,  "normal",   id="mention_required:on:mentioned_normal"),
    pytest.param(False, False, False, "normal",   id="mention_optional:off:no_mention_normal"),
    pytest.param(False, False, True,  "normal",   id="mention_optional:off:mentioned_normal"),
    pytest.param(False, True,  False, "judged",   id="mention_optional:on:no_mention_judged"),
    pytest.param(False, True,  True,  "normal",   id="mention_optional:on:mentioned_normal"),
]


@pytest.mark.parametrize("require_mention, autoreply, mentioned, expected", _ADMISSION_MATRIX)
def test_the_admission_matrix(monkeypatch, require_mention, autoreply, mentioned, expected):
    adapter, seen = _handoff_adapter(
        monkeypatch, autoreply_on=autoreply,
        require_mention=require_mention, mentioned=mentioned,
    )
    asyncio.run(adapter._handle_message_event_data(_event()))

    if expected == "dropped":
        assert seen == [], "the message should never reach inbound processing"
        return
    assert len(seen) == 1, "the message should reach inbound processing"
    assert seen[0]["jev_gate_pending"] is (expected == "judged")


def test_turning_autoreply_on_changes_only_the_unmentioned_rows(monkeypatch):
    # Guards the backward-compatibility claim above as one assertion rather
    # than eight: enabling the feature must not alter a mentioned message.
    changed = []
    for require_mention in (True, False):
        for mentioned in (True, False):
            outcomes = []
            for autoreply in (False, True):
                adapter, seen = _handoff_adapter(
                    monkeypatch, autoreply_on=autoreply,
                    require_mention=require_mention, mentioned=mentioned,
                )
                asyncio.run(adapter._handle_message_event_data(_event()))
                outcomes.append(
                    "dropped" if not seen
                    else ("judged" if seen[0]["jev_gate_pending"] else "normal")
                )
            if outcomes[0] != outcomes[1]:
                changed.append((require_mention, mentioned))
    assert changed == [(True, False), (False, False)]


def test_a_bot_sender_is_never_answered_unprompted(monkeypatch):
    adapter, seen = _handoff_adapter(monkeypatch, autoreply_on=True)
    asyncio.run(adapter._handle_message_event_data(_event(sender_type="bot")))
    assert seen == []


def test_a_normal_dm_does_not_arm_the_gate(monkeypatch):
    adapter, seen = _handoff_adapter(monkeypatch, autoreply_on=True)
    asyncio.run(adapter._handle_message_event_data(_event(chat_type="p2p")))
    assert len(seen) == 1
    assert seen[0]["jev_gate_pending"] is False


# --- the gate inside inbound processing ------------------------------------


def _inbound_adapter(monkeypatch, *, verdict, reply_thread: bool = True):
    """A bare adapter whose inbound dependencies are stubbed to constants."""
    import json
    from unittest.mock import AsyncMock, Mock

    from gateway.config import PlatformConfig
    from plugins.platforms.feishu.adapter import FeishuAdapter

    adapter = FeishuAdapter.__new__(FeishuAdapter)
    adapter.config = PlatformConfig(extra={})
    adapter._bot_open_id = "ou_bot"
    adapter._bot_user_id = ""
    adapter._bot_name = "Hermes"
    adapter._reply_thread_enabled_setting = reply_thread
    adapter._download_feishu_message_resources = AsyncMock(return_value=([], []))
    adapter._fetch_message_text = AsyncMock(return_value=None)
    adapter.get_chat_info = AsyncMock(return_value={"name": "Ops Room"})
    adapter._resolve_sender_profile = AsyncMock(
        return_value={"user_id": "u1", "user_name": "Alice", "user_id_alt": None}
    )
    adapter._resolve_source_chat_type = Mock(return_value="group")
    adapter.build_source = Mock(return_value=SimpleNamespace(thread_id=None))
    adapter._dispatch_inbound_event = AsyncMock()
    adapter._maybe_route_delegate_interaction_message = AsyncMock(return_value=False)
    adapter._maybe_route_delegate_foreground_message = AsyncMock(return_value=False)

    judged: list[str] = []

    async def _in_scope(*, text, chat_name, sender_name):
        judged.append(text)
        return verdict

    adapter._jev_message_in_scope = _in_scope
    adapter._judged = judged

    def _run(text="生产环境有一批告警需要研判"):
        message = SimpleNamespace(
            content=json.dumps({"text": text}),
            message_type="text",
            message_id="om_trigger",
            mentions=[],
            chat_id="oc_ops",
            parent_id=None,
            upper_message_id=None,
            thread_id=None,
        )
        asyncio.run(
            adapter._process_inbound_message(
                data=message,
                message=message,
                sender_id=None,
                chat_type="group",
                message_id="om_trigger",
                jev_gate_pending=True,
            )
        )

    return adapter, _run


def test_an_in_scope_message_is_dispatched_and_marked(monkeypatch):
    adapter, run = _inbound_adapter(monkeypatch, verdict=True)
    run()
    assert adapter._dispatch_inbound_event.await_count == 1
    event = adapter._dispatch_inbound_event.call_args.args[0]
    assert event.metadata["jev_channel_autoreply"] is True


def test_an_out_of_scope_message_is_never_dispatched(monkeypatch):
    adapter, run = _inbound_adapter(monkeypatch, verdict=False)
    run()
    assert adapter._dispatch_inbound_event.await_count == 0


def test_an_admitted_message_is_answered_in_a_topic_under_itself(monkeypatch):
    adapter, run = _inbound_adapter(monkeypatch, verdict=True)
    run()
    event = adapter._dispatch_inbound_event.call_args.args[0]
    assert event.source.thread_id == "om_trigger"


def test_the_topic_reply_is_forced_even_when_reply_thread_is_disabled(monkeypatch):
    # Answering in the open channel is what FEISHU_REPLY_THREAD=false asks for
    # when someone @-mentions us; an unprompted reply must stay in a topic.
    adapter, run = _inbound_adapter(monkeypatch, verdict=True, reply_thread=False)
    run()
    event = adapter._dispatch_inbound_event.call_args.args[0]
    assert event.source.thread_id == "om_trigger"


def test_an_unmentioned_slash_command_is_dropped_without_consulting_jev(monkeypatch):
    adapter, run = _inbound_adapter(monkeypatch, verdict=True)
    run(text="/reset")
    assert adapter._dispatch_inbound_event.await_count == 0
    assert adapter._judged == []
