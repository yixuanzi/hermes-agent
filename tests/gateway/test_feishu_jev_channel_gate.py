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


def _adapter_with_jev(monkeypatch, *, active: bool, verdict, gated: bool = False):
    from plugins.platforms.feishu import adapter as feishu_adapter
    from agent import jev_policy

    adapter = make_adapter_skeleton()
    monkeypatch.setattr(
        jev_policy, "load_jev_settings",
        lambda: SimpleNamespace(name="stub", autoreply_gate=gated),
    )
    monkeypatch.setattr(
        jev_policy, "channel_admission_mode",
        lambda _s, **_kw: "judge" if active else ("silence" if gated else "normal"),
    )

    async def _judge(text, **_kwargs):
        return verdict

    monkeypatch.setattr(jev_policy, "judge_channel_relevance_async", _judge)
    return adapter, feishu_adapter


def test_the_mode_is_normal_when_autoreply_is_not_active(monkeypatch):
    adapter, _ = _adapter_with_jev(monkeypatch, active=False, verdict=True)
    assert adapter._jev_admission_mode() == "normal"


def test_the_mode_is_judge_when_autoreply_is_active(monkeypatch):
    adapter, _ = _adapter_with_jev(monkeypatch, active=True, verdict=True)
    assert adapter._jev_admission_mode() == "judge"


def test_the_mode_is_silence_when_the_gate_is_required_but_inactive(monkeypatch):
    adapter, _ = _adapter_with_jev(monkeypatch, active=False, verdict=True, gated=True)
    assert adapter._jev_admission_mode() == "silence"


def test_a_raising_mode_check_falls_back_on_the_master_switch(monkeypatch):
    from agent import jev_policy

    adapter = make_adapter_skeleton()

    def _boom(_s, **_kw):
        raise RuntimeError("policy exploded")

    monkeypatch.setattr(jev_policy, "channel_admission_mode", _boom)

    monkeypatch.setattr(
        jev_policy, "load_jev_settings",
        lambda: SimpleNamespace(name="stub", autoreply_gate=False),
    )
    assert adapter._jev_admission_mode() == "normal"

    monkeypatch.setattr(
        jev_policy, "load_jev_settings",
        lambda: SimpleNamespace(name="stub", autoreply_gate=True),
    )
    assert adapter._jev_admission_mode() == "silence"


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


def test_unresolvable_settings_fall_back_to_the_normal_path(monkeypatch):
    adapter = make_adapter_skeleton()
    monkeypatch.setattr(type(adapter), "_jev_settings", lambda self: None)
    assert adapter._jev_admission_mode() == "normal"
    # The verdict stage stays fail-closed regardless.
    assert (
        asyncio.run(adapter._jev_message_in_scope(text="…", chat_name="", sender_name=""))
        is False
    )


# --- the admission hand-off ------------------------------------------------


def _handoff_adapter(
    monkeypatch, *, autoreply_on: bool, require_mention: bool = True,
    mentioned: bool = False, thread_on: bool = False, master_on: bool = True,
):
    """An adapter whose _process_inbound_message only records its kwargs.

    The autoreply gate is stubbed at the adapter boundary but keeps the real
    in_thread contract, so the matrix exercises how the adapter classifies a
    message rather than re-testing the policy's own flag handling.

    ``master_on`` defaults to True because the sub-switches only exist inside
    the master switch: with HERMES_JEV_AUTOREPLY off every mode is "normal",
    and a matrix over the sub-switches would be measuring nothing.
    """
    from tests.gateway.feishu_helpers import install_dedup_state

    adapter = make_adapter_skeleton(
        require_mention=require_mention, group_policy="open", allow_bots="all",
    )
    install_dedup_state(adapter)
    stub_mention(adapter, mentioned)
    monkeypatch.setattr(
        type(adapter),
        "_jev_admission_mode",
        lambda self, *, in_thread=False: (
            "normal" if not master_on
            else (
                "judge"
                if autoreply_on and (thread_on or not in_thread)
                else "silence"
            )
        ),
    )
    seen: list[dict] = []

    async def _process(**kwargs):
        seen.append(kwargs)

    adapter._process_inbound_message = _process
    return adapter, seen


def _event(*, sender_type="user", chat_type="group", message_id="om_1",
           thread_id=None, root_id=None):
    message = make_message(message_id=message_id, chat_type=chat_type)
    message.thread_id = thread_id
    message.root_id = root_id
    return SimpleNamespace(
        event=SimpleNamespace(
            sender=make_sender(sender_type=sender_type), message=message,
        )
    )


# The full admission matrix for a human group message, WITH the master switch
# on: require_mention x HERMES_JEV_CHANNEL_AUTOREPLY x whether the bot was
# @-mentioned. (Master off is _MASTER_MATRIX's job — it is "normal" throughout.)
#
# The row that matters is require_mention=False + autoreply=True + no mention.
# Tying the gate to the drop path alone left that combination unguarded — and
# require_mention=False is precisely the configuration where the bot would
# otherwise answer EVERY message in the group, so it is the one that most needs
# a business-scope filter. Every mentioned row must keep the exact behavior it
# had before the gate existed: the master switch is about UNADDRESSED messages.
_ADMISSION_MATRIX = [
    # require_mention, autoreply, mentioned, expected outcome
    pytest.param(True,  False, False, "dropped",  id="mention_required:off:no_mention_dropped"),
    pytest.param(True,  False, True,  "normal",   id="mention_required:off:mentioned_normal"),
    pytest.param(True,  True,  False, "judged",   id="mention_required:on:no_mention_judged"),
    pytest.param(True,  True,  True,  "normal",   id="mention_required:on:mentioned_normal"),
    pytest.param(False, False, False, "dropped",  id="mention_optional:off:no_mention_silenced"),
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
    # Guards the claim above as one assertion rather than eight: the channel
    # sub-switch must never alter a mentioned message.
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


def _inbound_adapter(monkeypatch, *, verdict, reply_thread: bool = True, busy: bool = False):
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
    # The real resolver needs a SessionSource; build_source is stubbed here, so
    # the boundary is stubbed too. `_thread_session_is_busy` itself is tested
    # directly further down.
    busy_checked: list[object] = []

    def _busy(source, *, root_message_id=None):
        busy_checked.append(root_message_id)
        return busy

    adapter._thread_session_is_busy = _busy
    adapter._busy_checked = busy_checked

    judged: list[str] = []

    async def _in_scope(*, text, chat_name, sender_name):
        judged.append(text)
        return verdict

    adapter._jev_message_in_scope = _in_scope
    adapter._judged = judged

    def _run(text="生产环境有一批告警需要研判", **markers):
        message = SimpleNamespace(
            content=json.dumps({"text": text}),
            message_type="text",
            message_id="om_trigger",
            mentions=[],
            chat_id="oc_ops",
            parent_id=None,
            upper_message_id=None,
            thread_id=markers.get("thread_id"),
            root_id=markers.get("root_id"),
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


# --- a topic that is already working is left alone ------------------------
#
# HERMES_JEV_THREAD_AUTOREPLY lets an unprompted message inside a topic be
# judged. It must not let one barge into a turn already running there: an
# @-mentioned follow-up is queued and answered after the current turn, but a
# message nobody addressed to us has no such claim, so it is dropped.


def test_a_busy_topic_is_dropped_without_consulting_jev(monkeypatch):
    adapter, run = _inbound_adapter(monkeypatch, verdict=True, busy=True)
    run(thread_id="omt_1")
    assert adapter._dispatch_inbound_event.await_count == 0
    assert adapter._judged == [], "the verdict could not have changed the outcome"


def test_an_idle_topic_is_judged_as_before(monkeypatch):
    adapter, run = _inbound_adapter(monkeypatch, verdict=True, busy=False)
    run(thread_id="omt_1")
    assert adapter._dispatch_inbound_event.await_count == 1
    assert len(adapter._judged) == 1


def test_a_busy_topic_that_is_out_of_scope_is_dropped_either_way(monkeypatch):
    adapter, run = _inbound_adapter(monkeypatch, verdict=False, busy=True)
    run(thread_id="omt_1")
    assert adapter._dispatch_inbound_event.await_count == 0


def test_a_top_level_message_is_never_busy_checked(monkeypatch):
    # The check is scoped to messages already inside a topic. A top-level
    # message opens a NEW topic, so there is no in-progress conversation of
    # its own to interrupt.
    adapter, run = _inbound_adapter(monkeypatch, verdict=True, busy=True)
    run()
    assert adapter._busy_checked == []
    assert adapter._dispatch_inbound_event.await_count == 1


@pytest.mark.parametrize("markers", [{"thread_id": "omt_1"}, {"root_id": "om_root"}])
def test_both_kinds_of_topic_marker_trigger_the_check(monkeypatch, markers):
    adapter, run = _inbound_adapter(monkeypatch, verdict=True, busy=True)
    run(**markers)
    assert len(adapter._busy_checked) == 1
    assert adapter._dispatch_inbound_event.await_count == 0


def test_the_root_id_is_handed_to_the_resolver(monkeypatch):
    # The busy check has to resolve root-keyed vs real-thread identity the same
    # way dispatch does, so it needs the root id, not just the thread id.
    adapter, run = _inbound_adapter(monkeypatch, verdict=True, busy=False)
    run(thread_id="omt_1", root_id="om_root")
    assert adapter._busy_checked == ["om_root"]


def test_a_mentioned_message_is_unaffected(monkeypatch):
    # jev_gate_pending is False for an @-mentioned message, so the busy check
    # never runs and the follow-up is queued by the normal path instead.
    adapter, run = _inbound_adapter(monkeypatch, verdict=True, busy=True)
    message_kwargs = {"thread_id": "omt_1"}
    import json
    from types import SimpleNamespace as NS
    message = NS(
        content=json.dumps({"text": "hi"}), message_type="text",
        message_id="om_trigger", mentions=[], chat_id="oc_ops",
        parent_id=None, upper_message_id=None, root_id=None, **message_kwargs,
    )
    asyncio.run(adapter._process_inbound_message(
        data=message, message=message, sender_id=None, chat_type="group",
        message_id="om_trigger", jev_gate_pending=False,
    ))
    assert adapter._busy_checked == []
    assert adapter._dispatch_inbound_event.await_count == 1


def test_an_unmentioned_slash_command_is_dropped_without_consulting_jev(monkeypatch):
    adapter, run = _inbound_adapter(monkeypatch, verdict=True)
    run(text="/reset")
    assert adapter._dispatch_inbound_event.await_count == 0
    assert adapter._judged == []


# --- the thread sub-switch -------------------------------------------------
#
# A message inside an existing topic is a conversation the agent is usually
# already part of, so judging every follow-up needs its own opt-in. The FIRST
# message of a topic carries neither thread_id nor root_id — the bot creates
# the topic from its own reply — so it is never gated by this sub-switch.


_THREAD_MATRIX = [
    # thread markers, thread_autoreply, expected
    pytest.param({}, False, "judged", id="top_level:thread_off_still_judged"),
    pytest.param({}, True, "judged", id="top_level:thread_on_judged"),
    pytest.param({"thread_id": "omt_1"}, False, "dropped", id="live_thread:off_not_judged"),
    pytest.param({"thread_id": "omt_1"}, True, "judged", id="live_thread:on_judged"),
    pytest.param({"root_id": "om_root"}, False, "dropped", id="rooted_thread:off_not_judged"),
    pytest.param({"root_id": "om_root"}, True, "judged", id="rooted_thread:on_judged"),
]


@pytest.mark.parametrize("markers, thread_on, expected", _THREAD_MATRIX)
def test_the_thread_sub_switch(monkeypatch, markers, thread_on, expected):
    adapter, seen = _handoff_adapter(
        monkeypatch, autoreply_on=True, require_mention=True, thread_on=thread_on,
    )
    asyncio.run(adapter._handle_message_event_data(_event(**markers)))

    if expected == "dropped":
        assert seen == [], "require_mention should drop it once the gate declines"
        return
    assert len(seen) == 1
    assert seen[0]["jev_gate_pending"] is True


def test_an_unopted_thread_message_is_silenced_under_the_master_switch(monkeypatch):
    # require_mention=False is where the bot would otherwise answer every
    # follow-up in the topic. Under the master switch, "not opted in" means
    # silence rather than an unjudged answer.
    adapter, seen = _handoff_adapter(
        monkeypatch, autoreply_on=True, require_mention=False, thread_on=False,
    )
    asyncio.run(adapter._handle_message_event_data(_event(thread_id="omt_1")))
    assert seen == []


def test_the_same_thread_message_is_answered_unjudged_without_the_master_switch(
    monkeypatch,
):
    # With HERMES_JEV_AUTOREPLY off nothing is judged and nothing is silenced;
    # the mention gate decides, exactly as it did before Jev existed.
    adapter, seen = _handoff_adapter(
        monkeypatch, autoreply_on=True, require_mention=False, thread_on=False,
        master_on=False,
    )
    asyncio.run(adapter._handle_message_event_data(_event(thread_id="omt_1")))
    assert len(seen) == 1
    assert seen[0]["jev_gate_pending"] is False, "answered, but never judged"


def test_the_thread_sub_switch_cannot_open_the_gate_on_its_own(monkeypatch):
    adapter, seen = _handoff_adapter(
        monkeypatch, autoreply_on=False, require_mention=True, thread_on=True,
    )
    asyncio.run(adapter._handle_message_event_data(_event(thread_id="omt_1")))
    assert seen == []


# --- the policy-level flag -------------------------------------------------


def test_thread_autoreply_defaults_to_off():
    from agent import jev_policy

    settings = jev_policy.JevSettings(
        api_key="k", agent_description="A SOC assistant.", channel_autoreply=True,
    )
    assert settings.thread_autoreply is False
    assert jev_policy.channel_autoreply_active(settings) is True
    assert jev_policy.channel_autoreply_active(settings, in_thread=True) is False


def test_thread_autoreply_on_opens_the_gate_inside_threads():
    from agent import jev_policy

    settings = jev_policy.JevSettings(
        api_key="k", agent_description="A SOC assistant.",
        channel_autoreply=True, thread_autoreply=True,
    )
    assert jev_policy.channel_autoreply_active(settings, in_thread=True) is True


def test_thread_autoreply_is_a_sub_switch_of_channel_autoreply():
    from agent import jev_policy

    settings = jev_policy.JevSettings(
        api_key="k", agent_description="A SOC assistant.",
        channel_autoreply=False, thread_autoreply=True,
    )
    assert jev_policy.channel_autoreply_active(settings) is False
    assert jev_policy.channel_autoreply_active(settings, in_thread=True) is False


# --- the master switch -----------------------------------------------------
#
# HERMES_JEV_AUTOREPLY is checked before anything else. Off, Jev is never
# consulted and the sub-switches are inert — every message takes the pre-Jev
# path. On, an unaddressed group message is answered ONLY with Jev's blessing;
# every other outcome, including the sub-switch simply being off, is silence.


_MASTER_MATRIX = [
    # master, channel_on, in_thread, thread_on, require_mention, expected
    pytest.param(False, False, False, False, False, "normal",
                 id="master_off:gate_off:answered_unjudged"),
    pytest.param(False, True, False, False, False, "normal",
                 id="master_off:gate_on:still_unjudged"),
    pytest.param(False, True, True, True, False, "normal",
                 id="master_off:thread_on:still_unjudged"),
    pytest.param(True, False, False, False, False, "dropped",
                 id="master_on:gate_off:silenced"),
    pytest.param(True, True, False, False, False, "judged",
                 id="master_on:gate_on:judged"),
    pytest.param(True, True, True, False, False, "dropped",
                 id="master_on:thread_not_covered:silenced"),
    pytest.param(True, True, True, True, False, "judged",
                 id="master_on:thread_covered:judged"),
    pytest.param(True, False, False, False, True, "dropped",
                 id="master_on:gate_off:mention_required_still_dropped"),
]


@pytest.mark.parametrize(
    "master, channel_on, in_thread, thread_on, require_mention, expected",
    _MASTER_MATRIX,
)
def test_the_master_switch_matrix(
    monkeypatch, master, channel_on, in_thread, thread_on, require_mention, expected
):
    adapter, seen = _handoff_adapter(
        monkeypatch, autoreply_on=channel_on, require_mention=require_mention,
        thread_on=thread_on, master_on=master,
    )
    markers = {"thread_id": "omt_1"} if in_thread else {}
    asyncio.run(adapter._handle_message_event_data(_event(**markers)))

    if expected == "dropped":
        assert seen == [], "the master switch should have silenced this"
        return
    assert len(seen) == 1
    assert seen[0]["jev_gate_pending"] is (expected == "judged")


def test_the_master_switch_never_silences_a_mentioned_message(monkeypatch):
    adapter, seen = _handoff_adapter(
        monkeypatch, autoreply_on=False, require_mention=False,
        mentioned=True, master_on=True,
    )
    asyncio.run(adapter._handle_message_event_data(_event()))
    assert len(seen) == 1
    assert seen[0]["jev_gate_pending"] is False


def test_the_master_switch_never_silences_a_dm(monkeypatch):
    adapter, seen = _handoff_adapter(
        monkeypatch, autoreply_on=False, require_mention=False, master_on=True,
    )
    asyncio.run(adapter._handle_message_event_data(_event(chat_type="p2p")))
    assert len(seen) == 1
    assert seen[0]["jev_gate_pending"] is False


# --- the busy check resolves the session dispatch will actually use --------
#
# Everything above stubs `_thread_session_is_busy` at the adapter boundary.
# These exercise the real thing, because its whole value is that it measures
# the SAME session key `handle_message` will look up. A check against the
# other candidate id would read an idle session and answer anyway.


def _busy_adapter(**extra):
    from gateway.config import PlatformConfig
    from gateway.platforms.base import Platform
    from plugins.platforms.feishu.adapter import FeishuAdapter

    adapter = FeishuAdapter.__new__(FeishuAdapter)
    adapter.config = PlatformConfig(extra=extra)
    # `platform` is what the stale-lock warning formats its prefix from, so a
    # skeleton without it turns a heal into an AttributeError.
    adapter.platform = Platform.FEISHU
    adapter._active_sessions = {}
    adapter._session_tasks = {}
    adapter._pending_messages = {}
    adapter._session_store = None
    return adapter


def _feishu_source(thread_id=None):
    from gateway.session import SessionSource
    from gateway.platforms.base import Platform

    return SessionSource(
        platform=Platform.FEISHU, chat_id="oc_ops", chat_type="group",
        user_id="u1", thread_id=thread_id,
    )


def test_an_idle_topic_reads_as_idle():
    adapter = _busy_adapter()
    assert adapter._thread_session_is_busy(_feishu_source("omt_1")) is False


def test_a_running_turn_in_the_same_topic_reads_as_busy():
    adapter = _busy_adapter()
    source = _feishu_source("omt_1")
    adapter._active_sessions[adapter._session_key_for_source(source)] = asyncio.Event()
    assert adapter._thread_session_is_busy(source) is True


def test_a_running_turn_in_a_DIFFERENT_topic_does_not_block_this_one():
    adapter = _busy_adapter()
    other = _feishu_source("omt_other")
    adapter._active_sessions[adapter._session_key_for_source(other)] = asyncio.Event()
    assert adapter._thread_session_is_busy(_feishu_source("omt_1")) is False


def test_the_check_follows_the_root_keyed_session_when_one_exists():
    # An auto-created topic's later messages key on the om_* root once that
    # session exists. The busy check must follow the same hop, or it would
    # look up the omt_* session and find it idle.
    adapter = _busy_adapter()
    root_keyed = _feishu_source("om_root")
    adapter._active_sessions[adapter._session_key_for_source(root_keyed)] = asyncio.Event()

    source = _feishu_source("omt_1")
    assert adapter._thread_session_is_busy(source, root_message_id="om_root") is True
    assert source.thread_id == "omt_1", "the probe must not mutate the source"


def test_a_human_created_topic_keeps_its_own_identity():
    # No root-keyed session exists, so the omt_* identity stands and a busy
    # om_root session is somebody else's conversation.
    adapter = _busy_adapter()
    adapter._active_sessions[
        adapter._session_key_for_source(_feishu_source("om_root"))
    ] = asyncio.Event()
    # ...but make the root-keyed probe fail by asking about a different root.
    assert adapter._thread_session_is_busy(
        _feishu_source("omt_1"), root_message_id="om_unrelated",
    ) is False


def test_the_root_keyed_probe_reports_whether_the_root_won():
    # None, not "the id that won": a caller that inferred the outcome from a
    # string comparison would read a tie as a win and mark a topic established
    # whose session does not exist.
    adapter = _busy_adapter()
    source = _feishu_source("omt_1")
    assert adapter._auto_thread_root_session(source, "om_root") is None

    adapter._active_sessions[
        adapter._session_key_for_source(_feishu_source("om_root"))
    ] = asyncio.Event()
    assert adapter._auto_thread_root_session(source, "om_root") == "om_root"
    assert source.thread_id == "omt_1", "the probe must not mutate the source"


def test_an_unbuildable_session_key_is_not_treated_as_busy():
    adapter = _busy_adapter()
    assert adapter._thread_session_is_busy(SimpleNamespace(thread_id=None)) is False


def test_a_raising_busy_check_falls_through_to_the_verdict():
    adapter = _busy_adapter()

    def _boom(_key):
        raise RuntimeError("bookkeeping exploded")

    adapter.session_has_running_task = _boom
    assert adapter._thread_session_is_busy(_feishu_source("omt_1")) is False


# --- the base adapter's busy predicate ------------------------------------


def test_session_has_running_task_heals_a_stale_lock():
    # A guard whose owner task already exited must not silence a topic
    # forever — it is a split-brain, not a running turn.
    adapter = _busy_adapter()
    adapter._active_sessions["k"] = asyncio.Event()
    adapter._session_tasks["k"] = SimpleNamespace(done=lambda: True)
    assert adapter.session_has_running_task("k") is False
    assert "k" not in adapter._active_sessions


def test_session_has_running_task_keeps_a_live_lock():
    adapter = _busy_adapter()
    adapter._active_sessions["k"] = asyncio.Event()
    adapter._session_tasks["k"] = SimpleNamespace(done=lambda: False)
    assert adapter.session_has_running_task("k") is True


@pytest.mark.parametrize("key", ["", "unknown"])
def test_session_has_running_task_is_false_for_a_key_with_no_guard(key):
    assert _busy_adapter().session_has_running_task(key) is False
