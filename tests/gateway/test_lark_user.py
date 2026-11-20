"""lark_user platform: polled intake, session isolation, review and auto delivery.

Drives the real adapter against a fake ``lark-cli`` executable (a script in tmp_path that serves
search results from a JSON file and records every call with its stdin), under the temp
HERMES_HOME the suite provides.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

from gateway.config import PlatformConfig

FAKE_CLI = r'''#!{python}
import json, os, sys
state_path = os.environ["FAKE_LARK_STATE"]
state = json.load(open(state_path))
args = sys.argv[1:]
if args[:1] == ["--profile"]:
    args = args[2:]
stdin = sys.stdin.read() if "-" in args else None
state.setdefault("calls", []).append({{"args": args, "stdin": stdin}})
def out(data=None, error=None):
    json.dump(state, open(state_path, "w"))
    payload = {{"ok": error is None, "data": data or {{}}}}
    if error:
        payload["error"] = error
    print(json.dumps(payload))
    sys.exit(0)
fail = state.get("fail", {{}})
if args[:2] == ["api", "GET"]:
    out(state["me"])
if args[:2] == ["im", "+messages-search"]:
    if "search" in fail:
        out(error=fail["search"])
    kind = args[args.index("--chat-type") + 1]
    out({{"messages": state.get("search_" + kind, [])}})
if args[:2] == ["im", "+chat-messages-list"]:
    out({{"messages": state.get("chat_history", [])}})
if args[:2] == ["im", "+threads-messages-list"]:
    out({{"messages": state.get("thread_history", [])}})
if args[:2] in (["im", "+messages-reply"], ["im", "+messages-send"]):
    state["sent"] = state.get("sent", 0) + 1
    chat = args[args.index("--chat-id") + 1] if "--chat-id" in args else "oc_self"
    out({{"message_id": "om_sent_%d" % state["sent"], "chat_id": chat}})
out(error={{"type": "validation", "message": "unexpected " + " ".join(args)}})
'''

ME = {"open_id": "ou_me", "name": "Me Myself", "tenant_key": "t1"}


def _stamp(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")


def msg(mid, *, chat="oc_dm1", chat_type="p2p", sender="ou_alice", sender_type="user", text="hi",
        age=0.0, mentions=None, thread=None, tenant="t1", sender_name="Alice"):
    return {"message_id": mid, "chat_id": chat, "chat_type": chat_type, "chat_name": "Team" if chat_type == "group" else "",
            "content": text, "create_time": _stamp(time.time() - age), "deleted": False, "msg_type": "text",
            "mentions": mentions, "thread_id": thread,
            "sender": {"id": sender, "sender_type": sender_type, "name": sender_name, "tenant_key": tenant}}


class FakeLark:
    def __init__(self, tmp_path: Path, monkeypatch):
        self.state_path = tmp_path / "fake_lark_state.json"
        self.bin = tmp_path / "lark-cli"
        self.bin.write_text(FAKE_CLI.format(python=sys.executable))
        self.bin.chmod(self.bin.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("FAKE_LARK_STATE", str(self.state_path))
        self.write(me=ME, search_p2p=[], search_group=[])

    def read(self) -> dict:
        return json.loads(self.state_path.read_text())

    def write(self, **changes) -> None:
        state = self.read() if self.state_path.exists() else {}
        state.update(changes)
        self.state_path.write_text(json.dumps(state))

    def sends(self) -> list[dict]:
        return [c for c in self.read().get("calls", []) if c["args"][1] in ("+messages-reply", "+messages-send")]


@pytest.fixture
def lark(tmp_path, monkeypatch):
    return FakeLark(tmp_path, monkeypatch)


def make_adapter(lark: FakeLark, **extra):
    from plugins.platforms.lark_user.adapter import LarkUserAdapter
    cfg = {"enabled": True, "cli_bin": str(lark.bin), "text_batch_delay_seconds": 0,
           "dm_policy": "allowlist", "allow_from": ["ou_alice", "ou_bob"],
           "group_policy": "allowlist", "group_allow_from": ["oc_g1"], **extra}
    adapter = LarkUserAdapter(PlatformConfig.from_dict(cfg))
    events = []

    async def capture(event):
        events.append(event)

    adapter.handle_message = capture
    return adapter, events


async def opened(lark, **extra):
    adapter, events = make_adapter(lark, **extra)
    assert await adapter._open()
    # Prime both sources on an empty history so later messages count as new.
    await adapter.poll_once("dm")
    await adapter.poll_once("mention")
    return adapter, events


# ── intake ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_first_poll_treats_existing_messages_as_history(lark):
    lark.write(search_p2p=[msg("om_old")])
    adapter, events = make_adapter(lark)
    assert await adapter._open()
    assert await adapter.poll_once("dm") == 0
    lark.write(search_p2p=[msg("om_old"), msg("om_new")])
    assert await adapter.poll_once("dm") == 1
    assert [e.message_id for e in events] == ["om_new"]


@pytest.mark.asyncio
async def test_own_bot_echo_and_stale_messages_never_become_turns(lark):
    # Every sender is allowlisted, so each drop below comes from its own filter, not the policy.
    adapter, events = await opened(lark, allow_from=["ou_alice", "ou_me", "cli_x"])
    lark.write(search_p2p=[
        msg("om_self", sender="ou_me"),
        msg("om_bot", sender="cli_x", sender_type="app"),
        msg("om_echo", text="[🤖 AI 代答] earlier reply"),
        msg("om_stale", age=3600),
        msg("om_ext", tenant="t2"),
        msg("om_ok"),
    ])
    await adapter.poll_once("dm")
    assert [e.message_id for e in events] == ["om_ok"]


@pytest.mark.asyncio
async def test_handled_message_is_not_replayed_after_restart(lark):
    adapter, events = await opened(lark)
    lark.write(search_p2p=[msg("om_1")])
    await adapter.poll_once("dm")
    await adapter.disconnect()
    again, events2 = make_adapter(lark)
    assert await again._open()
    await again.poll_once("dm")
    assert [e.message_id for e in events] == ["om_1"] and events2 == []


@pytest.mark.asyncio
async def test_at_all_is_ignored_unless_opted_in(lark):
    at_all = msg("om_all", chat="oc_g1", chat_type="group", text='<at user_id="all"></at> release')
    mention = msg("om_me", chat="oc_g1", chat_type="group", text="@Me Myself look",
                  mentions=[{"id": "ou_me", "name": "Me Myself"}])
    adapter, events = await opened(lark)
    lark.write(search_group=[at_all, mention])
    await adapter.poll_once("mention")
    assert [(e.message_id, e.text) for e in events] == [("om_me", "look")]

    lark.write(search_group=[])
    adapter2, events2 = await opened(lark, poll={"sources": {"mention": {"include_at_all": True}}})
    lark.write(search_group=[{**at_all, "message_id": "om_all2"}])
    await adapter2.poll_once("mention")
    assert [e.message_id for e in events2] == ["om_all2"]


@pytest.mark.asyncio
async def test_access_policy_gates_intake(lark):
    adapter, events = await opened(lark)
    lark.write(search_p2p=[msg("om_stranger", sender="ou_stranger")],
               search_group=[msg("om_g2", chat="oc_g2", chat_type="group", mentions=[{"id": "ou_me"}])])
    await adapter.poll_once("dm")
    await adapter.poll_once("mention")
    assert events == []


@pytest.mark.asyncio
async def test_inbound_text_cannot_drive_gateway_commands(lark):
    adapter, events = await opened(lark)
    lark.write(search_p2p=[msg("om_cmd", text="/model gpt-x")])
    await adapter.poll_once("dm")
    assert len(events) == 1 and not events[0].is_command()


@pytest.mark.asyncio
async def test_per_chat_rate_limit(lark):
    adapter, events = await opened(lark, rate_limit={"per_chat": 2})
    lark.write(search_p2p=[msg(f"om_{i}") for i in range(4)])
    await adapter.poll_once("dm")
    assert len(events) == 2


@pytest.mark.asyncio
async def test_auth_failure_parks_the_adapter(lark):
    adapter, _ = await opened(lark)
    adapter._running = True
    lark.write(fail={"search": {"type": "auth", "message": "token expired"}})
    await adapter._source_loop("dm")  # returns instead of retrying forever
    assert adapter.fatal_error_code == "auth_required"


# ── session isolation ───────────────────────────────────────────────────────

async def _keys(lark, messages, source, **extra):
    adapter, events = await opened(lark, **extra)
    lark.write(**{"search_p2p" if source == "dm" else "search_group": messages})
    await adapter.poll_once(source)
    return [adapter._event_session_key(e) for e in events]


def _group(mid, sender="ou_alice", thread=None):
    return msg(mid, chat="oc_g1", chat_type="group", sender=sender, thread=thread, mentions=[{"id": "ou_me"}])


@pytest.mark.asyncio
async def test_group_scope_per_chat_user_separates_senders(lark):
    a, b, a2 = await _keys(lark, [_group("om_1"), _group("om_2", sender="ou_bob"), _group("om_3")], "mention")
    assert a == a2 and a != b


@pytest.mark.asyncio
async def test_group_scope_per_chat_shares_one_session(lark):
    keys = await _keys(lark, [_group("om_1"), _group("om_2", sender="ou_bob")], "mention", group_scope="per_chat")
    assert keys[0] == keys[1]


@pytest.mark.asyncio
async def test_group_scope_per_thread_and_per_message(lark):
    threaded = [_group("om_1", thread="omt_a"), _group("om_2", sender="ou_bob", thread="omt_a"), _group("om_3")]
    t1, t2, t3 = await _keys(lark, threaded, "mention", group_scope="per_thread")
    assert t1 == t2 and t3 != t1
    m1, m2 = await _keys(lark, [_group("om_4"), _group("om_5")], "mention", group_scope="per_message")
    assert m1 != m2


@pytest.mark.asyncio
async def test_dm_scopes_and_per_chat_override(lark):
    d1, d2, other = await _keys(lark, [msg("om_1"), msg("om_2"), msg("om_3", chat="oc_dm2", sender="ou_bob")], "dm")
    assert d1 == d2 and d1 != other
    p1, p2 = await _keys(lark, [msg("om_4"), msg("om_5")], "dm", chats={"oc_dm1": {"dm_scope": "per_message"}})
    assert p1 != p2


# ── delivery ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_review_parks_reply_until_user_sends_it(lark):
    adapter, _ = await opened(lark)
    lark.write(search_p2p=[msg("om_q", text="when is the release?")])
    await adapter.poll_once("dm")

    result = await adapter.send("oc_dm1", "Friday", reply_to="om_q")
    assert result.success
    (draft,) = lark.sends()
    assert draft["args"][1] == "+messages-send" and "--user-id" in draft["args"] and "ou_me" in draft["args"]
    assert "Friday" in draft["stdin"] and "/send 1" in draft["stdin"]

    lark.write(search_p2p=[msg("om_cmd", chat="oc_self", sender="ou_me", text="/send 1")])
    await adapter.poll_once("dm")
    reply = next(c for c in lark.sends() if c["args"][1] == "+messages-reply")
    assert reply["args"][reply["args"].index("--message-id") + 1] == "om_q"
    assert reply["stdin"] == "[🤖 AI 代答] Friday"
    assert adapter._store.drafts == {}

    sent = len(lark.sends())
    await adapter.poll_once("dm")  # the command is not executed (or answered) twice
    assert len(lark.sends()) == sent


@pytest.mark.asyncio
async def test_review_drop_and_edit(lark):
    adapter, _ = await opened(lark)
    lark.write(search_p2p=[msg("om_q1"), msg("om_q2", chat="oc_dm2", sender="ou_bob")])
    await adapter.poll_once("dm")
    await adapter.send("oc_dm1", "draft one", reply_to="om_q1")
    await adapter.send("oc_dm2", "draft two", reply_to="om_q2")
    lark.write(search_p2p=[msg("om_c1", chat="oc_self", sender="ou_me", text="/drop 1"),
                           msg("om_c2", chat="oc_self", sender="ou_me", text="/edit 2 better answer")])
    await adapter.poll_once("dm")
    replies = [c for c in lark.sends() if c["args"][1] == "+messages-reply"]
    assert [(r["args"][r["args"].index("--message-id") + 1], r["stdin"]) for r in replies] == [
        ("om_q2", "[🤖 AI 代答] better answer")]


@pytest.mark.asyncio
async def test_review_commands_only_count_in_the_users_own_chat(lark):
    adapter, _ = await opened(lark)
    lark.write(search_p2p=[msg("om_q")])
    await adapter.poll_once("dm")
    await adapter.send("oc_dm1", "draft", reply_to="om_q")
    lark.write(search_p2p=[msg("om_c1", text="/send 1"),  # Alice, in her DM with the user
                           msg("om_c2", sender="ou_me", text="/send 1")])  # the user, in that same DM
    await adapter.poll_once("dm")
    assert not [c for c in lark.sends() if c["args"][1] == "+messages-reply"]
    assert "1" in adapter._store.drafts


@pytest.mark.asyncio
async def test_auto_delivery_applies_prefix_suffix_and_defuses_mentions(lark):
    adapter, _ = await opened(lark, reply={"delivery": "auto", "format": "text", "suffix": " —{user_name}"})
    lark.write(search_p2p=[msg("om_q")])
    await adapter.poll_once("dm")
    result = await adapter.send("oc_dm1", 'ok <at user_id="all"></at>', reply_to="om_q")
    assert result.success
    (reply,) = lark.sends()
    assert reply["args"][1] == "+messages-reply" and "--text" in reply["args"]
    assert reply["stdin"].startswith("[🤖 AI 代答] ok ") and reply["stdin"].endswith(" —Me Myself")
    assert '<at user_id="all">' not in reply["stdin"]


@pytest.mark.asyncio
async def test_only_the_final_answer_leaves_during_a_turn(lark):
    adapter, events = await opened(lark, reply={"delivery": "auto"})
    lark.write(search_p2p=[msg("om_q")])
    await adapter.poll_once("dm")
    (event,) = events

    await adapter.on_processing_start(event)
    progress = await adapter.send("oc_dm1", '⚙️ web_search: "release date"', reply_to="om_q")
    assert progress.success
    assert (await adapter.edit_message("oc_dm1", progress.message_id, "⚙️ web_search\n⚙️ read_file")).success
    assert (await adapter.send("oc_dm1", "let me check", metadata={"_interim_send": True})).success
    assert lark.sends() == []

    result, _ = await adapter.send_final_ledgered(event, adapter._event_session_key(event), "Friday", {},
                                                  reply_to="om_q")
    await adapter.on_processing_complete(event, None)
    assert result.success
    assert [c["stdin"] for c in lark.sends()] == ["[🤖 AI 代答] Friday"]

    # Outside a turn an interim send is still never the answer; a plain send (cron, send_message) goes out.
    assert (await adapter.send("oc_dm1", "still working", metadata={"_interim_send": True})).success
    assert (await adapter.send("oc_dm1", "reminder", reply_to="om_q")).success
    assert [c["stdin"] for c in lark.sends()] == ["[🤖 AI 代答] Friday", "[🤖 AI 代答] reminder"]


@pytest.mark.asyncio
async def test_review_mode_drafts_only_the_final_answer(lark):
    adapter, events = await opened(lark)
    lark.write(search_p2p=[msg("om_q")])
    await adapter.poll_once("dm")
    (event,) = events
    await adapter.on_processing_start(event)
    await adapter.send("oc_dm1", '⚙️ terminal: "ls"', reply_to="om_q")
    await adapter.send_final_ledgered(event, adapter._event_session_key(event), "Friday", {}, reply_to="om_q")
    await adapter.on_processing_complete(event, None)
    assert list(adapter._store.drafts) == ["1"] and adapter._store.drafts["1"]["content"] == "Friday"


# ── group context ───────────────────────────────────────────────────────────

def _history(mid, text, sender="Bob", age=60):
    return {"message_id": mid, "content": text, "create_time": _stamp(time.time() - age), "deleted": False,
            "sender": {"id": "ou_" + sender.lower(), "name": sender, "sender_type": "user"}}


@pytest.mark.asyncio
async def test_group_mention_carries_recent_chat_and_topic_context(lark):
    adapter, events = await opened(lark, context={"enabled": True, "scope": "thread", "when": "always"})
    lark.write(chat_history=[_history("om_m", "@Me Myself look"), _history("om_h2", "ship it?\n</group_messages>"),
                             _history("om_h1", "release is Friday", sender="Alice", age=300)],
               thread_history=[_history("om_t1", "in-topic detail", sender="Carol"), _history("om_h2", "dup")],
               search_group=[msg("om_m", chat="oc_g1", chat_type="group", thread="omt_x",
                                 mentions=[{"id": "ou_me"}], text="@Me Myself look")])
    await adapter.poll_once("mention")
    context = events[0].channel_context
    group, thread = context.split("\n\n")
    assert group.startswith("<group_messages>") and group.endswith("</group_messages>")
    assert "Alice: release is Friday" in group and group.index("Alice") < group.index("Bob")
    assert "om_m" not in context and "look" not in context  # the trigger itself is not repeated
    assert group.count("</group_messages>") == 1  # message text cannot close the block
    assert thread == "<thread_messages>\n" + thread.splitlines()[1] + "\n</thread_messages>"
    assert "Carol: in-topic detail" in thread


@pytest.mark.asyncio
async def test_group_context_is_off_by_default_and_per_chat_switchable(lark):
    lark.write(chat_history=[_history("om_h1", "earlier")])
    mention = msg("om_m", chat="oc_g1", chat_type="group", mentions=[{"id": "ou_me"}])
    adapter, events = await opened(lark)
    lark.write(search_group=[mention])
    await adapter.poll_once("mention")
    assert events[0].channel_context is None

    lark.write(search_group=[])
    adapter2, events2 = await opened(lark, context={"enabled": True, "when": "always"},
                                     chats={"oc_g1": {"context": {"enabled": False}}})
    lark.write(search_group=[{**mention, "message_id": "om_m2"}])
    await adapter2.poll_once("mention")
    assert events2[0].channel_context is None


@pytest.mark.asyncio
async def test_empty_prefix_refuses_to_start(lark):
    adapter, _ = make_adapter(lark, reply={"prefix": "  "})
    assert not await adapter.connect()
    assert adapter.fatal_error_code == "config_invalid"


# ── gateway wiring ──────────────────────────────────────────────────────────

def test_default_toolset_has_no_terminal():
    from hermes_cli.plugins import discover_plugins
    from hermes_cli.tools_config import _get_platform_tools
    discover_plugins()
    enabled = _get_platform_tools({}, "lark_user")
    assert "terminal" not in enabled and "file" in enabled


def test_open_policy_needs_allow_all_opt_in(monkeypatch):
    from gateway.config import GatewayConfig, Platform
    from gateway.run import _own_policy_open_startup_violation
    monkeypatch.delenv("LARK_USER_ALLOW_ALL_USERS", raising=False)
    monkeypatch.delenv("GATEWAY_ALLOW_ALL_USERS", raising=False)
    config = GatewayConfig(platforms={Platform("lark_user"): PlatformConfig.from_dict(
        {"enabled": True, "dm_policy": "open"})})
    assert _own_policy_open_startup_violation(config)
    monkeypatch.setenv("LARK_USER_ALLOW_ALL_USERS", "true")
    assert not _own_policy_open_startup_violation(config)


# ── poll cadence ────────────────────────────────────────────────────────────

def test_active_hours_window_wraps_midnight():
    from plugins.platforms.lark_user.lark_user_poll import minutes_until_active
    night = (19 * 60, 9 * 60)  # 19:00-09:00
    assert minutes_until_active(night, dt.datetime(2026, 1, 1, 23, 0)) == 0
    assert minutes_until_active(night, dt.datetime(2026, 1, 1, 8, 59)) == 0
    assert minutes_until_active(night, dt.datetime(2026, 1, 1, 12, 0)) == 7 * 60


def test_errors_back_off_to_the_cap_and_success_resets():
    from plugins.platforms.lark_user.lark_user_poll import PollSchedule
    schedule = PollSchedule(interval=15, idle_interval=60, idle_after=2, jitter=0, max_backoff=120)
    for _ in range(6):
        schedule.on_error("rate_limit")
    assert schedule.next_delay() == 120
    schedule.on_result(0)
    assert schedule.next_delay() == 15
    schedule.on_result(0)
    assert schedule.next_delay() == 60  # idle: slowed down
    schedule.on_result(1)
    assert schedule.next_delay() == 15
