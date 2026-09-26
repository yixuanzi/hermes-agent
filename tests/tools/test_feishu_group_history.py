"""tools.feishu_group_history — backend-independent history rendering.

Pins the pieces that must not drift between the two fetch backends: the
``FEISHU_GROUP_HISTORY_SOURCE`` spellings, the lark-cli argv and envelope
handling, stamp parsing for both ``create_time`` shapes, and that the API
and lark-cli backends render the same conversation to the same block.
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)

from tools import feishu_group_history as gh  # noqa: E402


# ---------------------------------------------------------------------------
# Source selection + stamps
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, "api"), ("", "api"), ("api", "api"), (" API ", "api"),
        ("cli", "cli"), ("CLI", "cli"), ("lark-cli", "cli"), ("LARK-CLI", "cli"), ("lark_cli", "cli"),
        ("feishu-cli", "cli"), ("feishu_cli", "cli"),
        ("graphql", "api"),  # unknown -> api, never a surprise subprocess
    ],
)
def test_normalize_history_source(raw, expected):
    assert gh.normalize_history_source(raw) == expected


def test_format_stamp_from_cli_keeps_month_day_time():
    assert gh.format_stamp_from_cli("2026-09-26 20:15") == "09-26 20:15"
    assert gh.format_stamp_from_cli("2026-09-26T20:15:00+08:00") == "09-26 20:15"
    assert gh.format_stamp_from_cli("") == ""
    assert gh.format_stamp_from_cli(None) == ""
    # A raw millisecond value still works through the CLI parser.
    millis = str(int(datetime(2026, 9, 26, 20, 15).timestamp() * 1000))
    assert gh.format_stamp_from_cli(millis) == "09-26 20:15"


def test_format_stamp_from_millis_rejects_garbage():
    assert gh.format_stamp_from_millis("abc") == ""
    assert gh.format_stamp_from_millis(0) == ""
    assert gh.format_stamp_from_millis(-5) == ""


# ---------------------------------------------------------------------------
# lark-cli backend
# ---------------------------------------------------------------------------


def _cli_payload(messages, **data):
    return {"ok": True, "identity": "bot", "data": {"has_more": False, "messages": messages, **data}}


def _cli_msg(message_id, content, *, sender_type="user", sender_id="ou_alice", name="Alice",
             create_time="2026-09-26 20:15", thread_id=None, deleted=False, msg_type="text", **extra):
    m = {
        "message_id": message_id,
        "msg_type": msg_type,
        "content": content,
        "create_time": create_time,
        "deleted": deleted,
        "sender": {"id": sender_id, "id_type": "open_id", "name": name, "sender_type": sender_type},
    }
    if thread_id:
        m["thread_id"] = thread_id
    m.update(extra)
    return m


def _runner(payload=None, *, code=0, stdout=None, stderr=b"", raise_exc=None):
    calls = []

    async def run(argv):
        calls.append(list(argv))
        if raise_exc is not None:
            raise raise_exc
        out = stdout if stdout is not None else json.dumps(payload).encode()
        return code, out, stderr

    run.calls = calls
    return run


def _cli_source(runner, binary="lark-cli"):
    return gh.LarkCliHistorySource(binary=binary, runner=runner)


def _with_binary(fn):
    with patch("tools.feishu_group_history.shutil.which", return_value="/opt/bin/lark-cli"):
        return fn()


def test_cli_chat_argv_shape_and_window():
    runner = _runner(_cli_payload([]))
    since = datetime(2026, 9, 26, 16, 0).timestamp()
    result = _with_binary(lambda: asyncio.run(_cli_source(runner).list_chat("oc_g", page_size=21, since_epoch=since)))
    assert result == []
    argv = runner.calls[0]
    assert argv[0] == "/opt/bin/lark-cli"
    assert argv[1:3] == ["im", "+chat-messages-list"]
    assert argv[argv.index("--chat-id") + 1] == "oc_g"
    start = argv[argv.index("--start") + 1]
    assert datetime.fromisoformat(start).timestamp() == since
    for flag, value in (("--as", "bot"), ("--order", "desc"), ("--page-size", "21"), ("--format", "json")):
        assert argv[argv.index(flag) + 1] == value
    assert "--no-reactions" in argv


def test_cli_thread_argv_shape_and_page_cap():
    runner = _runner(_cli_payload([]))
    _with_binary(lambda: asyncio.run(_cli_source(runner).list_thread("omt_1", page_size=99)))
    argv = runner.calls[0]
    assert argv[1:3] == ["im", "+threads-messages-list"]
    assert argv[argv.index("--thread") + 1] == "omt_1"
    assert argv[argv.index("--page-size") + 1] == "50"
    assert "--start" not in argv


def test_cli_parses_rendered_messages_and_ignores_inline_thread_replies():
    card = '<card title="🤖 Hermes">\n▶ 🔧 执行过程\n我是 **Hermes**\n</card>'
    messages = [
        _cli_msg("om_root", "@Bot 你是谁", thread_id="omt_1",
                 thread_replies=[_cli_msg("om_reply", "nested reply that must be ignored")]),
        _cli_msg("om_card", card, sender_type="app", sender_id="cli_self", name="Hermes",
                 msg_type="interactive", create_time="2026-09-26 20:16"),
        _cli_msg("om_gone", "recalled", deleted=True),
    ]
    runner = _runner(_cli_payload(messages))
    result = _with_binary(lambda: asyncio.run(_cli_source(runner).list_chat("oc_g", page_size=5, since_epoch=0)))

    assert [m.message_id for m in result] == ["om_root", "om_card", "om_gone"]
    root, card_msg, gone = result
    assert root.text == "@Bot 你是谁"
    assert root.sender_name == "Alice" and root.sender_type == "user" and root.sender_id == "ou_alice"
    assert root.stamp == "09-26 20:15"
    assert root.is_reply is False and root.in_thread is False  # thread_id alone marks "has a topic"
    assert card_msg.text == card  # interactive body survives as rendered text
    assert card_msg.sender_type == "app" and card_msg.sender_id == "cli_self"
    assert gone.deleted is True


def test_cli_marks_replies_when_root_or_parent_present():
    messages = [
        _cli_msg("om_r1", "quote reply", root_id="om_root"),
        _cli_msg("om_r2", "topic reply", parent_id="om_root", thread_id="omt_1"),
    ]
    runner = _runner(_cli_payload(messages))
    r1, r2 = _with_binary(lambda: asyncio.run(_cli_source(runner).list_chat("oc_g", page_size=5, since_epoch=0)))
    assert r1.is_reply and not r1.in_thread
    assert r2.is_reply and r2.in_thread


@pytest.mark.parametrize(
    "kwargs",
    [
        {"code": 1, "stderr": b"auth error"},
        {"stdout": b"not json"},
        {"payload": {"ok": False, "error": {"code": "token_missing"}}},
        {"payload": {"ok": True, "data": {"messages": "nope"}}, "expect": []},
        {"raise_exc": asyncio.TimeoutError()},
        {"raise_exc": OSError("spawn failed")},
    ],
)
def test_cli_failure_modes_yield_none_or_empty(kwargs):
    expect = kwargs.pop("expect", None)
    runner = _runner(kwargs.pop("payload", None), **kwargs)
    result = _with_binary(lambda: asyncio.run(_cli_source(runner).list_chat("oc_g", page_size=5, since_epoch=0)))
    assert result == expect


def test_cli_missing_binary_yields_none_without_running():
    runner = _runner(_cli_payload([]))
    with patch("tools.feishu_group_history.shutil.which", return_value=None):
        result = asyncio.run(_cli_source(runner, binary="definitely-not-installed").list_chat("oc_g", page_size=5, since_epoch=0))
    assert result is None
    assert runner.calls == []


def test_cli_absolute_binary_path_is_used_without_which():
    runner = _runner(_cli_payload([]))
    with patch("tools.feishu_group_history.shutil.which", return_value=None):
        asyncio.run(_cli_source(runner, binary="/custom/lark-cli").list_chat("oc_g", page_size=5, since_epoch=0))
    assert runner.calls[0][0] == "/custom/lark-cli"


# ---------------------------------------------------------------------------
# Rendering + parity between backends
# ---------------------------------------------------------------------------


def _render_ctx(resolver=None, max_chars=1000):
    return gh.HistoryRenderContext(
        app_id="cli_self",
        resolve_sender_name=resolver or AsyncMock(return_value=None),
        msg_max_chars=max_chars,
    )


def test_render_uses_backend_names_and_only_resolves_missing_ones():
    resolver = AsyncMock(side_effect=lambda sid: {"ou_bob": "Bob"}.get(sid))
    msgs = [  # newest first
        gh.HistoryMessage("m3", "from bot", sender_id="cli_self", sender_type="app", stamp="09-26 10:03"),
        gh.HistoryMessage("m2", "no name here", sender_id="ou_bob", sender_type="user", stamp="09-26 10:02"),
        gh.HistoryMessage("m1", "named", sender_id="ou_alice", sender_type="user", sender_name="Alice", stamp="09-26 10:01"),
    ]
    block, ids = asyncio.run(gh.render_history_block(
        msgs, exclude_ids=set(), limit=10, wrapper="group_messages", tag_replies=True, render=_render_ctx(resolver),
    ))
    assert block == (
        "<group_messages>\n"
        "[09-26 10:01] Alice: named\n"
        "[09-26 10:02] Bob: no name here\n"
        "[09-26 10:03] [assistant]: from bot\n"
        "</group_messages>"
    )
    assert ids == {"m1", "m2", "m3"}
    assert [c.args[0] for c in resolver.await_args_list] == ["ou_bob"]


def test_render_resolver_failure_falls_back_to_id():
    resolver = AsyncMock(side_effect=RuntimeError("contact api down"))
    msgs = [gh.HistoryMessage("m1", "hi", sender_id="ou_x", sender_type="user")]
    block, _ = asyncio.run(gh.render_history_block(
        msgs, exclude_ids=set(), limit=5, wrapper="group_messages", tag_replies=True, render=_render_ctx(resolver),
    ))
    assert block == "<group_messages>\nou_x: hi\n</group_messages>"


def test_api_and_cli_backends_render_identical_blocks():
    """Same conversation through both backends -> byte-identical block."""
    ts = datetime(2026, 9, 26, 20, 15)
    millis = str(int(ts.timestamp() * 1000))
    cli_stamp = ts.strftime("%Y-%m-%d %H:%M")

    api_items = [
        SimpleNamespace(message_id="om_2", msg_type="text", body=SimpleNamespace(content=json.dumps({"text": "answer"})),
                        mentions=None, sender=SimpleNamespace(id="cli_self", sender_type="app", sender_name="Hermes"),
                        create_time=millis, thread_id=None, root_id=None, deleted=False),
        SimpleNamespace(message_id="om_1", msg_type="text", body=SimpleNamespace(content=json.dumps({"text": "question"})),
                        mentions=None, sender=SimpleNamespace(id="ou_alice", sender_type="user", sender_name=None),
                        create_time=millis, thread_id=None, root_id=None, deleted=False),
    ]
    response = Mock()
    response.success = Mock(return_value=True)
    response.data = SimpleNamespace(items=api_items)
    client = Mock()
    client.im.v1.message.list = Mock(return_value=response)

    async def run_blocking(fn, *args):
        return fn(*args)

    def extract_text(*, msg_type, raw_content, mentions=None):
        return json.loads(raw_content)["text"]

    api_source = gh.ApiHistorySource(client=client, run_blocking=run_blocking, extract_text=extract_text, tz=None)

    cli_messages = [
        _cli_msg("om_2", "answer", sender_type="app", sender_id="cli_self", name="Hermes", create_time=cli_stamp),
        _cli_msg("om_1", "question", sender_id="ou_alice", name="Alice", create_time=cli_stamp),
    ]
    cli_source = _cli_source(_runner(_cli_payload(cli_messages)))

    settings = gh.GroupHistorySettings(enabled=True, limit=10, hours=24.0, thread_limit=0)
    resolver = AsyncMock(return_value="Alice")  # API backend has no name; CLI already does

    def build(source):
        return asyncio.run(gh.build_group_history_block(
            source=source, settings=settings, chat_id="oc_g", exclude_message_id="om_now", thread_id=None,
            render=_render_ctx(resolver), is_thread_anchor=lambda tid: str(tid).startswith("om_"),
        ))

    api_block = build(api_source)
    cli_block = _with_binary(lambda: build(cli_source))
    assert api_block == cli_block == (
        "<group_messages>\n"
        "[09-26 20:15] Alice: question\n"
        "[09-26 20:15] [assistant]: answer\n"
        "</group_messages>"
    )


def test_build_uses_thread_backend_and_dedupes_against_group_block():
    chat = [_cli_msg("om_now", "trigger", thread_id="omt_1"), _cli_msg("om_root", "root", thread_id="omt_1", create_time="2026-09-26 20:00")]
    topic = [
        _cli_msg("om_now", "trigger", thread_id="omt_1", root_id="om_root"),
        _cli_msg("om_ans", "card answer", sender_type="app", sender_id="cli_self", name="Hermes",
                 thread_id="omt_1", root_id="om_root", create_time="2026-09-26 20:10", msg_type="interactive"),
        _cli_msg("om_root", "root", thread_id="omt_1", create_time="2026-09-26 20:00"),
    ]
    calls = []

    async def runner(argv):
        calls.append(list(argv))
        payload = _cli_payload(topic) if "+threads-messages-list" in argv else _cli_payload(chat)
        return 0, json.dumps(payload).encode(), b""

    settings = gh.GroupHistorySettings(enabled=True, limit=10, hours=4.0, thread_limit=20)
    block = _with_binary(lambda: asyncio.run(gh.build_group_history_block(
        source=_cli_source(runner), settings=settings, chat_id="oc_g", exclude_message_id="om_now",
        thread_id="omt_1", render=_render_ctx(), is_thread_anchor=lambda tid: str(tid).startswith("om_"),
    )))
    assert block == (
        "<group_messages>\n[09-26 20:00] Alice: root\n</group_messages>\n\n"
        "<thread_messages>\n[09-26 20:10] [assistant]: card answer\n</thread_messages>"
    )
    assert [c[2] for c in calls] == ["+chat-messages-list", "+threads-messages-list"]


def test_build_skips_topic_for_om_anchor_and_returns_empty_on_chat_failure():
    runner = _runner(None, code=2, stderr=b"boom")
    settings = gh.GroupHistorySettings(enabled=True, thread_limit=20)
    block = _with_binary(lambda: asyncio.run(gh.build_group_history_block(
        source=_cli_source(runner), settings=settings, chat_id="oc_g", exclude_message_id="om_now",
        thread_id="omt_1", render=_render_ctx(), is_thread_anchor=lambda tid: str(tid).startswith("om_"),
    )))
    assert block == ""
    assert len(runner.calls) == 1  # topic never attempted after the chat listing failed
