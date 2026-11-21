"""lark_user platform: polled intake, session isolation, review and auto delivery.

Drives the real adapter against a fake ``lark-cli`` executable (a script in tmp_path that serves
search results from a JSON file and records every call with its stdin), under the temp
HERMES_HOME the suite provides.
"""

from __future__ import annotations

import asyncio
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
def save():  # whole-file replace: concurrent calls (two poll loops) must never interleave their writes
    tmp = "%s.%d" % (state_path, os.getpid())
    with open(tmp, "w") as fh:
        json.dump(state, fh)
    os.replace(tmp, state_path)
args = sys.argv[1:]
if args[:1] == ["--profile"]:
    args = args[2:]
stdin = sys.stdin.read() if "-" in args else None
state.setdefault("calls", []).append({{"args": args, "stdin": stdin,
                                       "env_app": os.environ.get("LARKSUITE_CLI_APP_ID", ""),
                                       "env_has_secret": bool(os.environ.get("LARKSUITE_CLI_APP_SECRET"))}})
def out(data=None, error=None):
    save()
    payload = {{"ok": error is None, "data": data or {{}}}}
    if error:
        payload["error"] = error
    print(json.dumps(payload))
    sys.exit(0)
fail = state.get("fail", {{}})
if args == ["config", "show"]:
    save()
    print(json.dumps({{"workspace": "hermes", "profile": "cli_user_app", "appId": "cli_user_app", "brand": "lark"}}))
    sys.exit(0)
if args[:3] == ["api", "GET", "/open-apis/authen/v1/user_info"]:
    if "user_info" in fail:
        out(error=fail["user_info"])
    out(state["me"])
if args[:3] == ["api", "GET", "/open-apis/bot/v3/info"]:
    out({{"bot": {{"app_name": "review-bot"}}}})
if args[:3] == ["api", "POST", "/open-apis/im/v1/messages"]:
    state["cards"] = state.get("cards", 0) + 1
    out({{"message_id": "om_card_%d" % state["cards"], "chat_id": "oc_botchat"}})
if args[:3] == ["im", "messages", "patch"]:
    out({{}})
if args[:3] == ["api", "POST", "/open-apis/interactive/v1/card/update"]:
    if "card_update" in fail:
        out(error=fail["card_update"])
    out({{}})
if args[:3] == ["event", "consume", "card.action.trigger"]:
    save()
    for event in state.get("card_events", []):
        print(json.dumps(event))
    sys.exit(0)
if args[:2] == ["im", "+messages-search"]:
    if "search" in fail:
        out(error=fail["search"])
    kind = args[args.index("--chat-type") + 1]
    out({{"messages": state.get("search_" + kind, [])}})
if args[:2] == ["im", "+chat-messages-list"]:
    out({{"messages": state.get("chat_history", [])}})
if args[:2] == ["im", "+threads-messages-list"]:
    if "asc" in args:  # topic root lookup: the topic's oldest message
        root = state.get("thread_roots", {{}}).get(args[args.index("--thread") + 1])
        out({{"messages": [{{"message_id": root}}] if root else []}})
    out({{"messages": state.get("thread_history", [])}})
if args[:2] in (["im", "+messages-reply"], ["im", "+messages-send"]):
    if "reply" in fail:
        out(error=fail["reply"])
    state["sent"] = state.get("sent", 0) + 1
    chat = args[args.index("--chat-id") + 1] if "--chat-id" in args else "oc_self"
    out({{"message_id": "om_sent_%d" % state["sent"], "chat_id": chat}})
out(error={{"type": "validation", "message": "unexpected " + " ".join(args)}})
'''

ME = {"open_id": "ou_me", "name": "Me Myself", "tenant_key": "t1", "union_id": "on_me"}


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
        tmp = self.state_path.with_suffix(".test")
        tmp.write_text(json.dumps(state))
        tmp.replace(self.state_path)

    def sends(self) -> list[dict]:
        return [c for c in self.read().get("calls", []) if c["args"][1:2] in (["+messages-reply"], ["+messages-send"])]


@pytest.fixture
def lark(tmp_path, monkeypatch):
    return FakeLark(tmp_path, monkeypatch)


def make_adapter(lark: FakeLark, *, capture: bool = True, **extra):
    from plugins.platforms.lark_user.adapter import LarkUserAdapter
    cfg = {"enabled": True, "cli_bin": str(lark.bin), "text_batch_delay_seconds": 0,
           "dm_policy": "allowlist", "allow_from": ["ou_alice", "ou_bob"],
           "group_policy": "allowlist", "group_allow_from": ["oc_g1"], **extra}
    adapter = LarkUserAdapter(PlatformConfig.from_dict(cfg))
    events = []

    async def capture_event(event):
        events.append(event)

    if capture:
        adapter.handle_message = capture_event
    return adapter, events


async def opened(lark, **extra):
    adapter, events = make_adapter(lark, **extra)
    assert await adapter._open()
    # Auto-reply switched on; these tests drive poll_once themselves rather than the poll loops
    # /lark-user start spawns (the switch's own tests below go through the command).
    adapter._active = True
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
async def test_pending_drafts_and_their_numbering_survive_a_restart(lark):
    adapter, _ = await opened(lark)
    lark.write(search_p2p=[msg("om_q1"), msg("om_q2")])
    await adapter.poll_once("dm")
    await adapter.send("oc_dm1", "first", reply_to="om_q1")
    await adapter.send("oc_dm1", "second", reply_to="om_q2")
    await adapter._desk.reject("2")
    await adapter.disconnect()

    again, _ = make_adapter(lark)
    assert await again._open()
    assert {n: d["content"] for n, d in again._store.drafts.items()} == {"1": "first"}
    assert again._store.drafts["1"]["review_msg"]  # where its card went, for the clicks to come
    again._active = True
    await again.send("oc_dm1", "third", reply_to="om_q1")
    assert sorted(again._store.drafts) == ["1", "3"]  # #2 is never handed out again


@pytest.mark.asyncio
async def test_a_legacy_json_state_file_is_imported_once(lark):
    from hermes_constants import get_hermes_home
    legacy = get_hermes_home() / "lark_user" / "ou_me.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({
        "watermarks": {"dm": time.time() - 60}, "seen": {"om_handled": time.time() - 60},
        "drafts": {"4": {"chat_id": "oc_dm1", "reply_to": "om_x", "content": "parked", "created_at": time.time()}},
        "review_chat": "oc_self", "next_draft": 5}))
    lark.write(search_p2p=[msg("om_handled"), msg("om_new")])
    adapter, events = make_adapter(lark)
    assert await adapter._open()
    adapter._active = True
    await adapter.poll_once("dm")  # already primed by the imported watermark

    assert [e.message_id for e in events] == ["om_new"]
    assert adapter._store.drafts["4"]["content"] == "parked" and adapter._store.review_chat == "oc_self"
    assert adapter._store.new_draft_number() == "5"
    assert not legacy.exists() and legacy.with_name("ou_me.json.migrated").exists()


def test_handled_ids_are_forgotten_after_their_ttl():
    from plugins.platforms.lark_user.lark_user_cursor import CursorStore
    from plugins.plugin_storage import plugin_db
    store = CursorStore(plugin_db("lark_user", "ttl.db"), seen_ttl=3600)
    store.mark_seen("om_old", now=time.time() - 7200)
    store.mark_seen("om_recent", now=time.time() - 60)
    store.prune()
    assert not store.is_seen("om_old") and store.is_seen("om_recent")


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
async def test_login_hint_targets_the_hermes_lark_cli_workspace(lark):
    # lark-cli switches to its "hermes" workspace whenever HERMES_HOME is set, so a plain
    # `lark-cli auth login` from a terminal would not fix the gateway's login.
    lark.write(fail={"user_info": {"type": "authentication", "message": "token expired"}})
    adapter, _ = make_adapter(lark)
    assert not await adapter.connect()
    assert adapter.fatal_error_code == "auth_required"
    assert "HERMES_HOME=" in adapter.fatal_error_message and "lark-cli auth login" in adapter.fatal_error_message


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
async def test_per_thread_root_mention_and_its_later_topic_share_one_session(lark):
    adapter, events = await opened(lark, group_scope="per_thread")
    lark.write(search_group=[_group("om_root")])  # an @mention before anyone replied to it
    await adapter.poll_once("mention")
    lark.write(thread_roots={"omt_X": "om_root", "omt_Y": "om_other"}, search_group=[
        _group("om_root"),
        _group("om_t1", thread="omt_X"),                 # the topic that grew under om_root
        _group("om_t2", sender="ou_bob", thread="omt_X"),
        _group("om_t3", thread="omt_Y"),                 # an unrelated topic
    ])
    await adapter.poll_once("mention")
    root, t1, t2, other = (adapter._event_session_key(e) for e in events)
    assert root == t1 == t2 and other != root
    lookups = [c for c in lark.read()["calls"] if c["args"][:2] == ["im", "+threads-messages-list"]]
    assert len(lookups) == 2  # once per topic


@pytest.mark.asyncio
async def test_revising_a_topic_draft_stays_in_the_topic_session(lark):
    adapter, events = await opened(lark, group_scope="per_thread")
    lark.write(thread_roots={"omt_X": "om_root"}, search_group=[_group("om_t1", thread="omt_X")])
    await adapter.poll_once("mention")
    original = events[-1]
    await adapter.send("oc_g1", "draft", reply_to="om_t1")
    await adapter._desk.revise("1", "更正式一点")
    assert adapter._event_session_key(events[-1]) == adapter._event_session_key(original)
    assert events[-1] is not original


@pytest.mark.asyncio
async def test_dm_scopes_and_per_chat_override(lark):
    d1, d2, other = await _keys(lark, [msg("om_1"), msg("om_2"), msg("om_3", chat="oc_dm2", sender="ou_bob")], "dm")
    assert d1 == d2 and d1 != other
    p1, p2 = await _keys(lark, [msg("om_4"), msg("om_5")], "dm", chats={"oc_dm1": {"dm_scope": "per_message"}})
    assert p1 != p2


# ── conversation lifetime ───────────────────────────────────────────────────

async def _with_store(lark, tmp_path, **extra):
    """An opened adapter wired to a real gateway SessionStore; ``turn`` stands in for the gateway
    opening the session of each event the adapter hands off, as the runner does when a turn starts."""
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    adapter, events = await opened(lark, **extra)
    store = SessionStore(tmp_path / "sessions", GatewayConfig())
    adapter.set_session_store(store)

    async def turn(source, *messages, age_after=0.0):
        lark.write(**{"search_p2p" if source == "dm" else "search_group": list(messages)})
        before = len(events)
        await adapter.poll_once(source)
        entries = [store.get_or_create_session(e.source) for e in events[before:]]
        for entry in entries:
            entry.updated_at = dt.datetime.now() - dt.timedelta(seconds=age_after)
        return entries

    return adapter, events, turn


@pytest.mark.asyncio
async def test_a_conversation_quiet_past_idle_reset_starts_afresh(lark, tmp_path):
    _, _, turn = await _with_store(lark, tmp_path, session={"idle_reset": "2h"})
    (first,) = await turn("dm", msg("om_1"))
    (soon,) = await turn("dm", msg("om_2"), age_after=3 * 3600)  # then nobody writes for 3h
    assert soon.session_id == first.session_id
    (later,) = await turn("dm", msg("om_3"))
    assert later.session_id != first.session_id and later.session_key == first.session_key
    (after,) = await turn("dm", msg("om_4"))
    assert after.session_id == later.session_id  # the new conversation carries on


@pytest.mark.asyncio
async def test_idle_reset_zero_keeps_a_chat_going_and_overrides_per_chat(lark, tmp_path):
    _, _, turn = await _with_store(lark, tmp_path, session={"idle_reset": "1h"},
                                   chats={"oc_dm2": {"session": {"idle_reset": 0}}})
    kept, ended = await turn("dm", msg("om_1", chat="oc_dm2", sender="ou_bob"), msg("om_2"), age_after=86400)
    kept2, ended2 = await turn("dm", msg("om_3", chat="oc_dm2", sender="ou_bob"), msg("om_4"))
    assert kept2.session_id == kept.session_id
    assert ended2.session_id != ended.session_id


@pytest.mark.asyncio
async def test_a_new_conversation_gets_group_context_again(lark, tmp_path):
    lark.write(chat_history=[_history("om_h1", "earlier")])
    _, events, turn = await _with_store(lark, tmp_path, session={"idle_reset": "1h"},
                                        context={"enabled": True, "when": "new_session"})
    await turn("mention", _group("om_1"))
    await turn("mention", _group("om_2"), age_after=7200)
    await turn("mention", _group("om_3"))
    assert [e.channel_context is not None for e in events] == [True, False, True]


def test_daily_reset_ends_what_was_last_active_before_the_hour():
    from plugins.platforms.lark_user.lark_user_config import ChatSettings
    from plugins.platforms.lark_user.lark_user_session import expiry_reason
    daily = ChatSettings(idle_reset=0, daily_reset_at=4 * 60)

    def at(day, hour, minute=0):
        return dt.datetime(2026, 1, day, hour, minute).timestamp()

    assert expiry_reason(at(1, 23), at(2, 5), daily) == "daily"
    assert expiry_reason(at(2, 4, 30), at(2, 5), daily) == ""
    assert expiry_reason(at(1, 5), at(2, 3), daily) == ""  # today's 04:00 has not come yet
    assert expiry_reason(at(1, 3), at(2, 3), daily) == "daily"
    assert expiry_reason(at(1, 23), at(2, 5), ChatSettings(idle_reset=0)) == ""


def test_reset_settings_are_validated():
    from plugins.platforms.lark_user.lark_user_config import SettingsError, load_settings
    with pytest.raises(SettingsError):
        load_settings({"session": {"idle_reset": "30s"}})
    with pytest.raises(SettingsError):
        load_settings({"session": {"daily_reset_at": "25:00"}})
    settings = load_settings({"session": {"daily_reset_at": "04:30"}, "chats": {"oc_x": {"session": {"daily_reset_at": None}}}})
    assert settings.defaults.idle_reset == 4 * 3600 and settings.defaults.daily_reset_at == 270
    assert settings.for_chat("oc_x").daily_reset_at is None


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


# ── review cards (separate review app) ─────────────────────────────────────

@pytest.fixture
def review_app(monkeypatch):
    from plugins.platforms.lark_user.lark_user_review_bot import ReviewBot
    monkeypatch.setenv("LARK_USER_INTERACTION_APP_ID", "cli_review")
    monkeypatch.setenv("LARK_USER_INTERACTION_APP_SECRET", "s3cret")

    async def token(self):
        return "t-test"

    monkeypatch.setattr(ReviewBot, "_tenant_token", token)


_BOT_HEADS = {"send": ["api", "POST", "/open-apis/im/v1/messages"], "patch": ["im", "messages", "patch"],
              "token": ["api", "POST", "/open-apis/interactive/v1/card/update"]}


def _bot_calls(lark, *verbs):
    heads = [_BOT_HEADS[v] for v in verbs]
    return [c for c in lark.read().get("calls", []) if c["args"][:3] in heads]


def _cards(lark):
    return [json.loads(json.loads(c["stdin"])["content"]) for c in _bot_calls(lark, "send")]


def _settled_card(call):
    body = json.loads(call["stdin"])
    return body["card"] if "token" in body else json.loads(body["content"])


def _patched(lark):
    """Every re-render of a review card, in order, whether by click token or by message patch."""
    return [_settled_card(c) for c in _bot_calls(lark, "patch", "token")]


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _buttons(card):
    return [n["name"] for n in _walk(card) if n.get("tag") == "button"]


def _text(card):
    return " ".join([card["header"]["title"]["content"]] + [n["content"] for n in _walk(card) if n.get("tag") == "markdown"])


def click(verb, number="1", notes="", message_id="om_card_1", chat="oc_botchat", event_id=None):
    return {"event_id": event_id or f"ev-{verb}-{number}-{time.time_ns()}", "message_id": message_id, "chat_id": chat,
            "token": f"c-token-{verb}-{number}",
            "action_value": json.dumps({"lark_user_review": verb, "draft": number}),
            "form_value": json.dumps({"revision": notes})}


async def _card_review(lark):
    adapter, events = await opened(lark)
    lark.write(search_p2p=[msg("om_q", text="when is the release?")])
    await adapter.poll_once("dm")
    assert (await adapter.send("oc_dm1", "Friday", reply_to="om_q")).success
    return adapter, events


@pytest.mark.asyncio
async def test_review_card_comes_from_the_review_app_with_three_choices(lark, review_app):
    await _card_review(lark)
    assert lark.sends() == []  # nothing reaches the other person yet
    (call,) = _bot_calls(lark, "send")
    assert "--as" in call["args"] and call["args"][call["args"].index("--as") + 1] == "bot"
    assert call["env_app"] == "cli_review" and call["env_has_secret"]
    assert not any("s3cret" in arg for c in lark.read()["calls"] for arg in c["args"])
    assert json.loads(call["stdin"])["receive_id"] == "on_me"
    (card,) = _cards(lark)
    assert _buttons(card) == ["approve", "revise", "reject"]
    assert any(n.get("tag") == "input" and n.get("name") == "revision" for n in _walk(card))
    assert "#1" in _text(card) and "Friday" in _text(card) and "when is the release?" in _text(card)


@pytest.mark.asyncio
async def test_approve_sends_the_reply_and_settles_the_card(lark, review_app):
    adapter, _ = await _card_review(lark)
    await adapter._on_review_click(click("approve"))
    (reply,) = lark.sends()
    assert reply["args"][reply["args"].index("--message-id") + 1] == "om_q"
    assert reply["stdin"] == "[🤖 AI 代答] Friday"
    (settled,) = _patched(lark)
    assert "已发送" in _text(settled) and _buttons(settled) == []
    assert adapter._store.drafts == {}


@pytest.mark.asyncio
async def test_a_clicked_card_is_rerendered_with_the_click_token(lark, review_app):
    adapter, _ = await _card_review(lark)
    await adapter._on_review_click(click("reject"))
    (update,) = _bot_calls(lark, "token")
    assert json.loads(update["stdin"])["token"] == "c-token-reject-1" and not _bot_calls(lark, "patch")

    assert (await adapter.send("oc_dm1", "Saturday", reply_to="om_q")).success
    lark.write(fail={"card_update": {"type": "api", "message": "token expired"}})
    await adapter._on_review_click(click("reject", number="2", message_id="om_card_2"))
    (patch,) = _bot_calls(lark, "patch")  # the token was refused: the message patch still settles the card
    assert json.loads(patch["args"][patch["args"].index("--params") + 1])["message_id"] == "om_card_2"
    assert "已拒绝" in _text(_settled_card(patch))


@pytest.mark.asyncio
async def test_failed_send_keeps_the_draft_for_a_retry(lark, review_app):
    adapter, _ = await _card_review(lark)
    lark.write(fail={"reply": {"type": "api", "message": "chat not found"}})
    await adapter._on_review_click(click("approve"))
    (failed,) = _patched(lark)
    assert "发送失败" in _text(failed) and _buttons(failed) == ["approve", "revise", "reject"]
    assert "1" in adapter._store.drafts
    lark.write(fail={})
    await adapter._on_review_click(click("approve"))
    assert "已发送" in _text(_patched(lark)[-1]) and adapter._store.drafts == {}


@pytest.mark.asyncio
async def test_reject_sends_nothing(lark, review_app):
    adapter, _ = await _card_review(lark)
    await adapter._on_review_click(click("reject"))
    assert lark.sends() == []
    (settled,) = _patched(lark)
    assert "已拒绝" in _text(settled) and _buttons(settled) == []
    assert adapter._store.drafts == {}


@pytest.mark.asyncio
async def test_revise_sends_notes_back_to_the_conversation_and_resubmits(lark, review_app):
    adapter, events = await _card_review(lark)
    original = events[-1]
    await adapter._on_review_click(click("revise", notes="说具体时间：周五下午三点"))
    assert lark.sends() == []
    (settled,) = _patched(lark)
    assert "修订中" in _text(settled) and _buttons(settled) == []

    revision = events[-1]
    assert revision is not original and "说具体时间：周五下午三点" in revision.text and "Friday" in revision.text
    assert revision.message_id == "om_q#rev1" and revision.reply_anchor_override == "om_q"
    assert not revision.is_command()
    assert adapter._event_session_key(revision) == adapter._event_session_key(original)

    await adapter.send("oc_dm1", "周五下午三点发版", reply_to="om_q")  # the rewritten final
    second = _cards(lark)[-1]
    assert "#2" in _text(second) and "第 1 次修订" in _text(second) and "周五下午三点" in _text(second)
    assert _buttons(second) == ["approve", "revise", "reject"]
    await adapter._on_review_click(click("approve", number="2", message_id="om_card_2"))
    assert [c["stdin"] for c in lark.sends()] == ["[🤖 AI 代答] 周五下午三点发版"]


@pytest.mark.asyncio
async def test_revise_without_notes_keeps_the_draft_open(lark, review_app):
    adapter, events = await _card_review(lark)
    count = len(events)
    await adapter._on_review_click(click("revise", notes="  "))
    (settled,) = _patched(lark)
    assert "修改意见" in _text(settled) and _buttons(settled) == ["approve", "revise", "reject"]
    assert "1" in adapter._store.drafts and len(events) == count


@pytest.mark.asyncio
async def test_only_a_click_on_the_drafts_own_card_counts(lark, review_app):
    adapter, _ = await _card_review(lark)
    await adapter._on_review_click(click("approve", message_id="om_other_card"))
    await adapter._on_review_click(click("approve", chat="oc_someone_else"))
    assert lark.sends() == [] and "1" in adapter._store.drafts
    twice = click("approve", event_id="ev-same")
    await adapter._on_review_click(twice)
    await adapter._on_review_click(twice)  # a redelivered click: the draft is already decided
    await adapter._on_review_click(click("reject"))
    assert len(lark.sends()) == 1 and len(_patched(lark)) == 1


@pytest.mark.asyncio
async def test_click_listener_feeds_card_events_to_the_desk(lark, review_app):
    adapter, _ = await _card_review(lark)
    lark.write(card_events=[click("approve")])
    await adapter._review_bot._consume_once(adapter._on_review_click)
    assert [c["stdin"] for c in lark.sends()] == ["[🤖 AI 代答] Friday"]


@pytest.mark.asyncio
async def test_card_review_needs_its_own_app(lark, monkeypatch, review_app):
    adapter, _ = make_adapter(lark)
    monkeypatch.setenv("FEISHU_APP_ID", "cli_review")
    assert not await adapter.connect()
    assert adapter.fatal_error_code == "config_invalid"

    monkeypatch.delenv("FEISHU_APP_ID")
    monkeypatch.setenv("LARK_USER_INTERACTION_APP_ID", "cli_user_app")  # the app lark-cli's user login runs on
    adapter2, _ = make_adapter(lark)
    assert not await adapter2.connect()
    assert adapter2.fatal_error_code == "config_invalid"

    monkeypatch.delenv("LARK_USER_INTERACTION_APP_ID")
    adapter3, _ = make_adapter(lark, review={"interactive": "card"})
    assert not await adapter3.connect()
    assert adapter3.fatal_error_code == "config_invalid"


# ── /lark-user: in-memory auto-reply switch ─────────────────────────────────

def _source(platform="local", user_id="u_local", user_id_alt=None, profile=None):
    from gateway.config import Platform
    from gateway.session import SessionSource
    return SessionSource(platform=Platform(platform), chat_id="c", chat_type="dm", user_id=user_id,
                         user_id_alt=user_id_alt, profile=profile)


def _searches(lark):
    return [c for c in lark.read().get("calls", []) if c["args"][:2] == ["im", "+messages-search"]]


@pytest.fixture
def fast_polls(monkeypatch):
    from plugins.platforms.lark_user.lark_user_poll import PollSchedule
    monkeypatch.setattr(PollSchedule, "next_delay", lambda self: 0.05)


@pytest.mark.asyncio
async def test_auto_reply_is_off_after_connect_and_nothing_is_polled(lark):
    from plugins.platforms.lark_user.lark_user_control import lark_user_command
    adapter, _ = make_adapter(lark)
    assert await adapter.connect()
    try:
        assert _searches(lark) == []
        status = await lark_user_command("status", source=_source())
        assert "已关闭" in status and "/lark-user start" in status
    finally:
        await adapter.disconnect()


@pytest.mark.asyncio
async def test_start_answers_only_what_arrives_while_on(lark, fast_polls):
    from plugins.platforms.lark_user.lark_user_control import lark_user_command
    from tests.waiting import async_wait_until
    adapter, events = make_adapter(lark)
    lark.write(search_p2p=[msg("om_before_start")])
    assert await adapter.connect()
    try:
        assert "已开启" in await lark_user_command("start", source=_source())
        await async_wait_until(lambda: not adapter._reprime, what="first poll takes history in")
        lark.write(search_p2p=[msg("om_before_start"), msg("om_while_on")])
        await async_wait_until(lambda: [e.message_id for e in events] == ["om_while_on"], what="new message answered")

        assert "已关闭" in await lark_user_command("stop", source=_source())
        assert all(task.done() for task in adapter._tasks + adapter._poll_tasks) and not adapter._poll_tasks
        lark.write(search_p2p=[msg("om_before_start"), msg("om_while_on"), msg("om_while_off")])
        assert "已开启" in await lark_user_command("start", source=_source())
        await async_wait_until(lambda: not adapter._reprime, what="restart takes history in")
        lark.write(search_p2p=[msg("om_before_start"), msg("om_while_on"), msg("om_while_off"), msg("om_back_on")])
        await async_wait_until(lambda: events[-1].message_id == "om_back_on", what="answered after restart")
        assert [e.message_id for e in events] == ["om_while_on", "om_back_on"]
    finally:
        await adapter.disconnect()


@pytest.mark.asyncio
async def test_switched_off_produces_no_reply_and_no_draft(lark):
    auto, _ = await opened(lark, reply={"delivery": "auto"})
    await auto.stop_auto_reply()
    assert (await auto.send("oc_dm1", "late answer", reply_to="om_q")).success
    assert lark.sends() == []

    review, _ = await opened(lark)
    await review.stop_auto_reply()
    await review.send("oc_dm1", "late draft", reply_to="om_q")
    assert review._store.drafts == {} and lark.sends() == []


@pytest.mark.asyncio
async def test_stop_interrupts_turns_in_flight_and_drops_queued_input(lark):
    adapter, events = await opened(lark)
    lark.write(search_p2p=[msg("om_q")])
    await adapter.poll_once("dm")
    (turn,) = events
    await adapter.on_processing_start(turn)  # the agent is working on it
    key = adapter._event_session_key(turn)
    adapter._pending_messages[key] = turn  # and a follow-up is queued behind it

    reply = await adapter.stop_auto_reply()
    stop = events[-1]
    assert stop.text == "/stop" and stop.is_command() and adapter._event_session_key(stop) == key
    assert adapter._pending_messages == {} and "中断了 1" in reply


class _BlockingRunner:
    """Stands in for the gateway runner: a turn blocks until cancelled; /stop is accepted the way
    the runner's stop handler accepts it, which is what lets the base adapter cancel the turn."""

    def __init__(self):
        self.seen: list[str] = []
        self.started, self.cancelled = asyncio.Event(), asyncio.Event()

    async def handle(self, event):
        self.seen.append(event.text)
        if event.text.startswith("/"):
            event._interrupt_accepted = True
            return
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


@pytest.mark.asyncio
async def test_stop_cancels_a_running_turn_through_the_session_guard(lark):
    adapter, _ = make_adapter(lark, capture=False)
    runner = _BlockingRunner()
    adapter.set_message_handler(runner.handle)
    assert await adapter._open()
    adapter._active = True
    await adapter.poll_once("dm")
    lark.write(search_p2p=[msg("om_q")])
    await adapter.poll_once("dm")  # the real handle_message starts the turn
    await asyncio.wait_for(runner.started.wait(), 5)

    reply = await adapter.stop_auto_reply()
    await asyncio.wait_for(runner.cancelled.wait(), 5)
    assert "/stop" in runner.seen and "中断了 1" in reply


@pytest.mark.asyncio
async def test_switched_off_refuses_revisions_but_keeps_decisions(lark, review_app):
    adapter, events = await _card_review(lark)
    await adapter.stop_auto_reply()
    count = len(events)
    await adapter._on_review_click(click("revise", notes="改短一点"))
    (paused,) = _patched(lark)
    assert "自动回复已关闭" in _text(paused) and _buttons(paused) == ["approve", "revise", "reject"]
    assert len(events) == count and "1" in adapter._store.drafts  # the agent did not run again
    await adapter._on_review_click(click("reject"))
    assert adapter._store.drafts == {}


@pytest.mark.asyncio
async def test_only_the_user_it_speaks_for_can_flip_the_switch(lark):
    from plugins.platforms.lark_user.lark_user_control import lark_user_command
    adapter, _ = make_adapter(lark)
    assert await adapter.connect()
    try:
        stranger = _source("feishu", user_id="ou_colleague", user_id_alt="on_colleague")
        assert "只有" in await lark_user_command("start", source=stranger) and not adapter._active
        assert "已开启" in await lark_user_command("start", source=_source("feishu", "ou_other_app_id", "on_me"))
        assert "已关闭" in await lark_user_command("stop", source=_source("feishu", "ou_me"))
        assert "没有在这个" in await lark_user_command("status", source=_source(profile="work"))
        assert "用法" in await lark_user_command("restart", source=_source())
    finally:
        await adapter.disconnect()



# ── on-demand reset (/lark-user reset) ──────────────────────────────────────

@pytest.fixture
def live():
    """Registers adapters for /lark-user the way connect() does, and unregisters them afterwards."""
    from plugins.platforms.lark_user.lark_user_control import register_live, unregister_live
    adapters = []

    def add(adapter):
        register_live(adapter)
        adapters.append(adapter)

    yield add
    for adapter in adapters:
        unregister_live(adapter)


def _conversation(adapter, chat, *, chat_type="group", user="ou_alice", thread=None, shared=False):
    import dataclasses
    source = adapter.build_source(chat_id=chat, chat_name="Team", chat_type=chat_type, user_id=user,
                                  user_name="Alice", thread_id=thread)
    return dataclasses.replace(source, shared_session=True) if shared else source


@pytest.mark.asyncio
async def test_reset_ends_every_live_conversation_of_one_chat_whatever_its_scope(lark, tmp_path, live):
    from plugins.platforms.lark_user.lark_user_control import lark_user_command
    adapter, _, _ = await _with_store(lark, tmp_path)
    store = adapter._session_store
    group = [_conversation(adapter, "oc_Team1"), _conversation(adapter, "oc_Team1", user="ou_bob"),
             _conversation(adapter, "oc_Team1", thread="om_root"), _conversation(adapter, "oc_Team1", shared=True)]
    others = [_conversation(adapter, "oc_other"), _conversation(adapter, "oc_dm1", chat_type="dm")]
    before = [store.get_or_create_session(s) for s in group + others]
    assert len({e.session_key for e in before}) == 6
    live(adapter)

    reply = await lark_user_command("reset oc_Team1", source=_source())
    after = [store.get_or_create_session(s) for s in group + others]
    assert "4 个会话" in reply
    assert [a.session_id != b.session_id for a, b in zip(after, before)] == [True] * 4 + [False] * 2
    assert "没有进行中的会话" in await lark_user_command("reset oc_nothing", source=_source())


@pytest.mark.asyncio
async def test_reset_without_a_chat_lists_the_chats_with_live_conversations(lark, tmp_path, live):
    from plugins.platforms.lark_user.lark_user_control import lark_user_command
    adapter, _, _ = await _with_store(lark, tmp_path)
    for source in (_conversation(adapter, "oc_g1"), _conversation(adapter, "oc_g1", user="ou_bob"),
                   _conversation(adapter, "oc_dm1", chat_type="dm")):
        adapter._session_store.get_or_create_session(source)
    live(adapter)
    listing = await lark_user_command("reset", source=_source())
    assert "群「Team」 oc_g1：2 个会话" in listing and "私聊「Alice」 oc_dm1：1 个会话" in listing
    await lark_user_command("reset oc_g1", source=_source())
    assert "oc_g1" not in await lark_user_command("reset", source=_source())


class _AnsweringRunner(_BlockingRunner):
    """Like the gateway runner, answers the commands it handles: those answers must never reach Lark."""

    async def handle(self, event):
        if event.text.startswith("/"):
            await super().handle(event)
            return "⏹ Stopped."
        return await super().handle(event)


async def _live_turn_adapter(lark, tmp_path):
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    adapter, _ = make_adapter(lark, capture=False)
    runner = _AnsweringRunner()
    adapter.set_message_handler(runner.handle)
    adapter.set_session_store(SessionStore(tmp_path / "sessions", GatewayConfig()))
    assert await adapter._open()
    adapter._active = True
    await adapter.poll_once("dm")
    return adapter, runner


@pytest.mark.asyncio
async def test_reset_interrupts_a_running_turn_and_its_stop_answer_stays_home(lark, tmp_path):
    adapter, runner = await _live_turn_adapter(lark, tmp_path)
    lark.write(search_p2p=[msg("om_q")])
    await adapter.poll_once("dm")
    await asyncio.wait_for(runner.started.wait(), 5)
    (source,) = adapter._inflight.values()
    adapter._session_store.get_or_create_session(source)  # the runner opened it when the turn began

    reply = await adapter.reset_conversations("oc_dm1")
    await asyncio.wait_for(runner.cancelled.wait(), 5)
    assert "中断了 1 个" in reply and "/stop" in runner.seen
    assert lark.sends() == []


@pytest.mark.asyncio
async def test_a_stop_that_finds_the_turn_already_over_answers_nobody(lark, tmp_path):
    from tests.waiting import async_wait_until
    adapter, runner = await _live_turn_adapter(lark, tmp_path)
    source = _conversation(adapter, "oc_dm1", chat_type="dm")
    key = adapter._session_store.get_or_create_session(source).session_key
    adapter._inflight[key] = source  # the turn ended between the reset's look and its /stop

    await adapter.reset_conversations("oc_dm1")
    await async_wait_until(lambda: "/stop" in runner.seen and not adapter._active_sessions, what="/stop handled")
    assert lark.sends() == []

def test_lark_user_command_is_registered_when_the_platform_loads():
    from gateway.platform_registry import platform_registry
    from hermes_cli.plugins import discover_plugins, get_plugin_command_handler
    discover_plugins()
    assert platform_registry.get("lark_user") is not None  # what the gateway does for an enabled platform
    assert get_plugin_command_handler("lark-user") is not None


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


def test_platform_is_not_enabled_without_an_explicit_opt_in(monkeypatch):
    from gateway.config import GatewayConfig, Platform
    from gateway.config_env import _enable_plugin_platform
    from gateway.platform_registry import platform_registry
    from hermes_cli.plugins import discover_plugins
    discover_plugins()
    entry = platform_registry.get("lark_user")
    monkeypatch.setattr(entry, "check_fn", lambda: True)  # lark-cli is installed

    config = GatewayConfig()
    _enable_plugin_platform(config, entry)
    assert Platform("lark_user") not in config.platforms

    config = GatewayConfig(platforms={Platform("lark_user"): PlatformConfig.from_dict({"dm_policy": "open"})})
    _enable_plugin_platform(config, entry)
    assert not config.platforms[Platform("lark_user")].enabled

    config = GatewayConfig(platforms={Platform("lark_user"): PlatformConfig.from_dict({"enabled": True})})
    assert Platform("lark_user") in config.get_connected_platforms()


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
