"""Feishu recent-group-history injection (``FEISHU_GROUP_HISTORY``).

A root (non-topic) group message can carry the group's recent messages,
wrapped in ``<group_messages>``, ahead of the user text.  These tests pin the
opt-in settings, the request the adapter sends, how history items are
rendered, the inbound gate, that batching keeps one block per turn, and
that the gateway's ``<source>`` header still comes first.
"""

import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

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

from gateway.config import GatewayConfig, Platform, PlatformConfig  # noqa: E402
from gateway.platforms.base import MessageEvent, MessageType  # noqa: E402
from gateway.session import SessionSource  # noqa: E402
from plugins.platforms.feishu.adapter import (  # noqa: E402
    _FEISHU_GROUP_HISTORY_METADATA_KEY,
    FeishuAdapter,
)

BLOCK = "<group_messages>\n[09-26 10:00] Alice: earlier\n</group_messages>"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def _clear_env(monkeypatch):
    for name in (
        "FEISHU_GROUP_HISTORY",
        "FEISHU_GROUP_HISTORY_LIMIT",
        "FEISHU_GROUP_HISTORY_HOURS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_group_history_is_opt_in(monkeypatch):
    _clear_env(monkeypatch)
    settings = FeishuAdapter._load_settings({})
    assert settings.group_history_enabled is False
    assert settings.group_history_limit == 20
    assert settings.group_history_hours == 24.0


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
def test_group_history_env_turns_on(monkeypatch, raw):
    _clear_env(monkeypatch)
    monkeypatch.setenv("FEISHU_GROUP_HISTORY", raw)
    assert FeishuAdapter._load_settings({}).group_history_enabled is True


def test_group_history_extra_beats_env(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("FEISHU_GROUP_HISTORY", "true")
    monkeypatch.setenv("FEISHU_GROUP_HISTORY_LIMIT", "10")
    monkeypatch.setenv("FEISHU_GROUP_HISTORY_HOURS", "3")
    settings = FeishuAdapter._load_settings(
        {"group_history_enabled": False, "group_history_limit": 7, "group_history_hours": 1.5}
    )
    assert settings.group_history_enabled is False
    assert settings.group_history_limit == 7
    assert settings.group_history_hours == 1.5


@pytest.mark.parametrize(
    "raw, expected",
    [("0", 20), ("-3", 20), ("abc", 20), ("5", 5), ("500", 50)],
)
def test_group_history_limit_is_clamped(monkeypatch, raw, expected):
    _clear_env(monkeypatch)
    monkeypatch.setenv("FEISHU_GROUP_HISTORY_LIMIT", raw)
    assert FeishuAdapter._load_settings({}).group_history_limit == expected


@pytest.mark.parametrize(
    "raw, expected",
    [("0", 24.0), ("-1", 24.0), ("nan", 24.0), ("garbage", 24.0), ("0.5", 0.5), ("72", 72.0)],
)
def test_group_history_hours_must_be_positive(monkeypatch, raw, expected):
    _clear_env(monkeypatch)
    monkeypatch.setenv("FEISHU_GROUP_HISTORY_HOURS", raw)
    assert FeishuAdapter._load_settings({}).group_history_hours == expected


@pytest.mark.parametrize(
    "raw, expected",
    [(None, 1000), ("0", 0), ("-5", 1000), ("abc", 1000), ("250", 250)],
)
def test_group_history_msg_max_chars_setting(monkeypatch, raw, expected):
    _clear_env(monkeypatch)
    monkeypatch.delenv("FEISHU_GROUP_HISTORY_MSG_MAX_CHARS", raising=False)
    if raw is not None:
        monkeypatch.setenv("FEISHU_GROUP_HISTORY_MSG_MAX_CHARS", raw)
    assert FeishuAdapter._load_settings({}).group_history_msg_max_chars == expected


@pytest.mark.parametrize(
    "raw, expected",
    [(None, 20), ("0", 0), ("-1", 20), ("abc", 20), ("7", 7), ("500", 50)],
)
def test_group_history_thread_limit_setting(monkeypatch, raw, expected):
    _clear_env(monkeypatch)
    monkeypatch.delenv("FEISHU_GROUP_HISTORY_THREAD_LIMIT", raising=False)
    if raw is not None:
        monkeypatch.setenv("FEISHU_GROUP_HISTORY_THREAD_LIMIT", raw)
    assert FeishuAdapter._load_settings({}).group_history_thread_limit == expected


@pytest.mark.parametrize(
    "raw, expected",
    [(None, "api"), ("api", "api"), ("cli", "cli"), ("lark-cli", "cli"), ("feishu-cli", "cli"), ("bogus", "api")],
)
def test_group_history_source_setting(monkeypatch, raw, expected):
    _clear_env(monkeypatch)
    monkeypatch.delenv("FEISHU_GROUP_HISTORY_SOURCE", raising=False)
    if raw is not None:
        monkeypatch.setenv("FEISHU_GROUP_HISTORY_SOURCE", raw)
    assert FeishuAdapter._load_settings({}).group_history_source == expected


def test_group_history_cli_bin_setting(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.delenv("FEISHU_GROUP_HISTORY_CLI_BIN", raising=False)
    assert FeishuAdapter._load_settings({}).group_history_cli_bin == "lark-cli"
    monkeypatch.setenv("FEISHU_GROUP_HISTORY_CLI_BIN", "/opt/lark/lark-cli")
    assert FeishuAdapter._load_settings({}).group_history_cli_bin == "/opt/lark/lark-cli"
    assert FeishuAdapter._load_settings({"group_history_cli_bin": "feishu-cli"}).group_history_cli_bin == "feishu-cli"
    assert FeishuAdapter._load_settings({"group_history_cli_bin": "   "}).group_history_cli_bin == "/opt/lark/lark-cli"


def test_adapter_picks_backend_from_source_setting():
    from tools import feishu_group_history as gh

    adapter = _history_adapter()
    api_settings = adapter._group_history_settings()
    assert api_settings.source == "api"
    assert isinstance(adapter._build_group_history_source(api_settings), gh.ApiHistorySource)

    adapter._group_history_source = "lark-cli"
    adapter._group_history_cli_bin = "/opt/lark/lark-cli"
    cli_settings = adapter._group_history_settings()
    assert cli_settings.source == "cli" and cli_settings.cli_bin == "/opt/lark/lark-cli"
    backend = adapter._build_group_history_source(cli_settings)
    assert isinstance(backend, gh.LarkCliHistorySource)
    assert backend._binary == "/opt/lark/lark-cli"


def test_adapter_cli_mode_never_touches_sdk_list_and_renders_cli_content():
    """End to end through the adapter wrapper in cli mode with a fake CLI runner."""
    from tools import feishu_group_history as gh

    adapter = _history_adapter()
    adapter._group_history_source = "cli"
    adapter._client.im.v1.message.list = Mock(side_effect=AssertionError("SDK must not be used in cli mode"))
    payload = {"ok": True, "data": {"messages": [
        {"message_id": "om_1", "msg_type": "interactive", "content": '<card title="x">\nbody\n</card>',
         "create_time": "2026-09-26 20:15", "deleted": False,
         "sender": {"id": "cli_self", "sender_type": "app", "name": "Hermes"}},
    ]}}

    async def runner(argv):
        return 0, json.dumps(payload).encode(), b""

    real_builder = adapter._build_group_history_source

    def patched_builder(settings):
        backend = real_builder(settings)
        backend._runner = runner
        return backend

    adapter._build_group_history_source = patched_builder
    with patch("tools.feishu_group_history.shutil.which", return_value="/opt/bin/lark-cli"):
        block = asyncio.run(adapter._fetch_group_history_block(chat_id="oc_g", exclude_message_id="om_now"))
    assert block == '<group_messages>\n[09-26 20:15] [assistant]: <card title="x"> body </card>\n</group_messages>'
    assert gh.METADATA_KEY == _FEISHU_GROUP_HISTORY_METADATA_KEY


def test_group_history_new_knobs_extra_beats_env(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("FEISHU_GROUP_HISTORY_MSG_MAX_CHARS", "10")
    monkeypatch.setenv("FEISHU_GROUP_HISTORY_THREAD_LIMIT", "10")
    settings = FeishuAdapter._load_settings(
        {"group_history_msg_max_chars": 0, "group_history_thread_limit": 3}
    )
    assert settings.group_history_msg_max_chars == 0
    assert settings.group_history_thread_limit == 3


# ---------------------------------------------------------------------------
# Fetch + format
# ---------------------------------------------------------------------------


def _history_adapter(*, limit=20, hours=24.0, msg_max_chars=1000, thread_limit=20):
    adapter = FeishuAdapter.__new__(FeishuAdapter)
    adapter._app_id = "cli_self"
    adapter._bot_open_id = "ou_bot"
    adapter._bot_user_id = ""
    adapter._bot_name = "Hermes"
    adapter._group_history_enabled = True
    adapter._group_history_limit = limit
    adapter._group_history_hours = hours
    adapter._group_history_msg_max_chars = msg_max_chars
    adapter._group_history_thread_limit = thread_limit
    adapter._client = Mock()
    adapter._resolve_sender_name_from_api = AsyncMock(
        side_effect=lambda sender_id, **_: {"ou_alice": "Alice", "ou_bob": "Bob"}.get(sender_id)
    )

    async def _run_blocking(func, *args):
        return func(*args)

    adapter._run_blocking = _run_blocking
    return adapter


def _item(
    message_id,
    text,
    *,
    sender_id="ou_alice",
    sender_type="user",
    sender_name=None,
    create_time="1758852000000",
    thread_id=None,
    root_id=None,
    deleted=False,
    msg_type="text",
):
    content = json.dumps({"text": text}) if msg_type == "text" else text
    return SimpleNamespace(
        message_id=message_id,
        msg_type=msg_type,
        body=SimpleNamespace(content=content),
        mentions=None,
        sender=SimpleNamespace(id=sender_id, id_type="open_id", sender_type=sender_type, sender_name=sender_name),
        create_time=create_time,
        thread_id=thread_id,
        root_id=root_id,
        deleted=deleted,
    )


def _ok_response(items):
    response = Mock()
    response.success = Mock(return_value=True)
    response.data = SimpleNamespace(items=items)
    return response


def test_fetch_builds_chat_scoped_desc_request_with_window():
    adapter = _history_adapter(limit=5, hours=2.0)
    adapter._client.im.v1.message.list = Mock(return_value=_ok_response([]))

    before = time.time()
    result = asyncio.run(adapter._fetch_group_history_block(chat_id="oc_g", exclude_message_id="om_now"))
    after = time.time()

    assert result == ""
    request = adapter._client.im.v1.message.list.call_args.args[0]
    assert request.container_id_type == "chat"
    assert request.container_id == "oc_g"
    assert request.sort_type == "ByCreateTimeDesc"
    assert request.page_size == 6  # limit + 1 leaves room to drop the trigger
    start = int(request.start_time)
    assert int(before - 2 * 3600) - 1 <= start <= int(after - 2 * 3600) + 1


def test_fetch_page_size_never_exceeds_api_cap():
    adapter = _history_adapter(limit=50)
    adapter._client.im.v1.message.list = Mock(return_value=_ok_response([]))
    asyncio.run(adapter._fetch_group_history_block(chat_id="oc_g", exclude_message_id="om_now"))
    assert adapter._client.im.v1.message.list.call_args.args[0].page_size == 50


def test_fetch_renders_chronological_block_with_labels():
    adapter = _history_adapter(limit=10)
    # API returns newest first.
    items = [
        _item("om_now", "the trigger itself", create_time="1758852300000"),
        _item("om_5", "in a topic", sender_id="ou_bob", root_id="om_1", thread_id="omt_1",
              create_time="1758852270000"),
        _item("om_4", "quoted reply", sender_id="ou_bob", root_id="om_1", create_time="1758852240000"),
        _item("om_3", "peer bot says hi", sender_id="cli_other", sender_type="app", sender_name="Other\nBot",
              create_time="1758852180000"),
        _item("om_2", "my own reply", sender_id="cli_self", sender_type="app", create_time="1758852120000"),
        _item("om_gone", "deleted", deleted=True, create_time="1758852090000"),
        # A root post that has a topic under it carries thread_id but no root_id: no tag.
        _item("om_1", "hello\nworld", thread_id="omt_1", create_time="1758852060000"),
    ]
    adapter._client.im.v1.message.list = Mock(return_value=_ok_response(items))

    with patch("hermes_time.get_timezone", return_value=None):
        block = asyncio.run(adapter._fetch_group_history_block(chat_id="oc_g", exclude_message_id="om_now"))

    lines = block.split("\n")
    assert lines[0] == "<group_messages>"
    assert lines[-1] == "</group_messages>"
    body = lines[1:-1]
    assert len(body) == 5
    # Chronological, trigger and deleted item excluded, newline collapsed.
    assert body[0].endswith("] Alice: hello world")
    assert body[1].endswith("[assistant]: my own reply")
    assert body[2].endswith("[bot] Other Bot: peer bot says hi")
    assert body[3].endswith("[reply] Bob: quoted reply")
    assert body[4].endswith("[in-topic] Bob: in a topic")
    # Each line carries a timestamp stamp.
    assert all(line.startswith("[") and "] " in line for line in body)
    assert "the trigger itself" not in block
    assert "deleted" not in block
    # Name lookups are deduplicated per sender and skipped for apps.
    looked_up = [c.args[0] for c in adapter._resolve_sender_name_from_api.await_args_list]
    assert sorted(looked_up) == ["ou_alice", "ou_bob"]


def test_fetch_respects_limit_after_excluding_trigger():
    adapter = _history_adapter(limit=2)
    items = [
        _item("om_now", "trigger"),
        _item("om_3", "three"),
        _item("om_2", "two"),
        _item("om_1", "one"),
    ]
    adapter._client.im.v1.message.list = Mock(return_value=_ok_response(items))
    block = asyncio.run(adapter._fetch_group_history_block(chat_id="oc_g", exclude_message_id="om_now"))
    body = block.split("\n")[1:-1]
    assert [line.rsplit(": ", 1)[1] for line in body] == ["two", "three"]


def test_fetch_falls_back_to_sender_id_when_name_unknown():
    adapter = _history_adapter()
    adapter._resolve_sender_name_from_api = AsyncMock(return_value=None)
    adapter._client.im.v1.message.list = Mock(
        return_value=_ok_response([_item("om_1", "hi", sender_id="ou_stranger")])
    )
    block = asyncio.run(adapter._fetch_group_history_block(chat_id="oc_g", exclude_message_id="om_now"))
    assert "ou_stranger: hi" in block


def test_fetch_returns_empty_on_failed_response():
    adapter = _history_adapter()
    response = Mock()
    response.success = Mock(return_value=False)
    response.code = 99991663
    response.msg = "forbidden"
    adapter._client.im.v1.message.list = Mock(return_value=response)
    assert asyncio.run(adapter._fetch_group_history_block(chat_id="oc_g", exclude_message_id="om_now")) == ""


def test_fetch_returns_empty_on_exception():
    adapter = _history_adapter()
    adapter._client.im.v1.message.list = Mock(side_effect=RuntimeError("boom"))
    assert asyncio.run(adapter._fetch_group_history_block(chat_id="oc_g", exclude_message_id="om_now")) == ""


def test_fetch_returns_empty_without_client_or_chat():
    adapter = _history_adapter()
    adapter._client = None
    assert asyncio.run(adapter._fetch_group_history_block(chat_id="oc_g", exclude_message_id="om_now")) == ""
    adapter = _history_adapter()
    assert asyncio.run(adapter._fetch_group_history_block(chat_id="", exclude_message_id="om_now")) == ""


# ---------------------------------------------------------------------------
# Topic (thread) history + per-message cap
# ---------------------------------------------------------------------------


def _thread_fixture():
    """Chat page with a root that has a topic; topic page with the trigger, two replies and the root."""
    chat_items = [
        _item("om_now", "trigger", thread_id="omt_1", root_id="om_root", create_time="1758852400000"),
        _item("om_root", "root post", thread_id="omt_1", create_time="1758852000000"),
    ]
    thread_items = [
        _item("om_now", "trigger", thread_id="omt_1", root_id="om_root", create_time="1758852400000"),
        _item("om_r2", "bot answer", sender_id="cli_self", sender_type="app", thread_id="omt_1", root_id="om_root",
              create_time="1758852300000"),
        _item("om_r1", "bob asks", sender_id="ou_bob", thread_id="omt_1", root_id="om_root",
              create_time="1758852200000"),
        _item("om_root", "root post", thread_id="omt_1", create_time="1758852000000"),
    ]
    return chat_items, thread_items


def _run_fetch(adapter, **kwargs):
    with patch("hermes_time.get_timezone", return_value=None):
        return asyncio.run(
            adapter._fetch_group_history_block(chat_id="oc_g", exclude_message_id="om_now", **kwargs)
        )


def test_fetch_appends_thread_block_without_repeating_group_items():
    adapter = _history_adapter(thread_limit=20)
    chat_items, thread_items = _thread_fixture()
    adapter._client.im.v1.message.list = Mock(
        side_effect=[_ok_response(chat_items), _ok_response(thread_items)]
    )

    block = _run_fetch(adapter, thread_id="omt_1")

    group_part, thread_part = block.split("\n\n")
    assert group_part.startswith("<group_messages>\n") and group_part.endswith("\n</group_messages>")
    assert thread_part.startswith("<thread_messages>\n") and thread_part.endswith("\n</thread_messages>")
    group_lines = group_part.split("\n")[1:-1]
    thread_lines = thread_part.split("\n")[1:-1]
    assert [line.rsplit(": ", 1)[1] for line in group_lines] == ["root post"]
    # Chronological, trigger excluded, root not repeated, no reply tags inside a topic.
    assert [line.rsplit(": ", 1)[1] for line in thread_lines] == ["bob asks", "bot answer"]
    assert thread_lines[0].endswith("] Bob: bob asks")
    assert thread_lines[1].endswith("] [assistant]: bot answer")
    assert "[in-topic]" not in thread_part and "[reply]" not in thread_part

    calls = adapter._client.im.v1.message.list.call_args_list
    assert len(calls) == 2
    thread_request = calls[1].args[0]
    assert thread_request.container_id_type == "thread"
    assert thread_request.container_id == "omt_1"
    assert thread_request.sort_type == "ByCreateTimeDesc"
    assert thread_request.page_size == 21
    assert thread_request.start_time is None  # no time window for the topic itself


def test_fetch_thread_limit_bounds_topic_lines_and_page_size():
    adapter = _history_adapter(thread_limit=1)
    chat_items, thread_items = _thread_fixture()
    adapter._client.im.v1.message.list = Mock(
        side_effect=[_ok_response(chat_items), _ok_response(thread_items)]
    )
    block = _run_fetch(adapter, thread_id="omt_1")
    thread_part = block.split("\n\n")[1]
    assert thread_part.split("\n")[1:-1] == [
        line for line in thread_part.split("\n")[1:-1] if line.endswith("bot answer")
    ]
    assert len(thread_part.split("\n")[1:-1]) == 1  # newest surviving topic message only
    assert adapter._client.im.v1.message.list.call_args_list[1].args[0].page_size == 2


def test_fetch_topic_lines_do_not_count_against_group_limit():
    adapter = _history_adapter(limit=1, thread_limit=20)
    chat_items, thread_items = _thread_fixture()
    adapter._client.im.v1.message.list = Mock(
        side_effect=[_ok_response(chat_items), _ok_response(thread_items)]
    )
    block = _run_fetch(adapter, thread_id="omt_1")
    group_part, thread_part = block.split("\n\n")
    assert len(group_part.split("\n")[1:-1]) == 1
    assert len(thread_part.split("\n")[1:-1]) == 2


@pytest.mark.parametrize("kwargs", [{"thread_limit": 0}, {}])
def test_fetch_skips_topic_call_when_disabled_or_not_in_topic(kwargs):
    adapter = _history_adapter(**kwargs)
    chat_items, _ = _thread_fixture()
    adapter._client.im.v1.message.list = Mock(return_value=_ok_response(chat_items))
    thread_id = "omt_1" if kwargs else None
    block = _run_fetch(adapter, thread_id=thread_id)
    assert adapter._client.im.v1.message.list.call_count == 1
    assert "<thread_messages>" not in block


def test_fetch_skips_topic_call_for_om_anchor():
    adapter = _history_adapter()
    chat_items, _ = _thread_fixture()
    adapter._client.im.v1.message.list = Mock(return_value=_ok_response(chat_items))
    block = _run_fetch(adapter, thread_id="om_root")
    assert adapter._client.im.v1.message.list.call_count == 1
    assert "<thread_messages>" not in block


def test_fetch_topic_failure_keeps_group_block():
    adapter = _history_adapter()
    chat_items, _ = _thread_fixture()
    failed = Mock()
    failed.success = Mock(return_value=False)
    failed.code = 1
    failed.msg = "nope"
    adapter._client.im.v1.message.list = Mock(side_effect=[_ok_response(chat_items), failed])
    block = _run_fetch(adapter, thread_id="omt_1")
    assert block.startswith("<group_messages>") and block.endswith("</group_messages>")
    assert "<thread_messages>" not in block


def test_fetch_topic_only_when_group_window_is_empty():
    adapter = _history_adapter()
    _, thread_items = _thread_fixture()
    adapter._client.im.v1.message.list = Mock(
        side_effect=[_ok_response([]), _ok_response(thread_items)]
    )
    block = _run_fetch(adapter, thread_id="omt_1")
    assert block.startswith("<thread_messages>")
    # Nothing was shown in the group block, so the root appears in the topic block.
    assert [line.rsplit(": ", 1)[1] for line in block.split("\n")[1:-1]] == ["root post", "bob asks", "bot answer"]


def test_fetch_group_failure_returns_empty_and_skips_topic():
    adapter = _history_adapter()
    failed = Mock()
    failed.success = Mock(return_value=False)
    failed.code = 1
    failed.msg = "nope"
    adapter._client.im.v1.message.list = Mock(return_value=failed)
    assert _run_fetch(adapter, thread_id="omt_1") == ""
    assert adapter._client.im.v1.message.list.call_count == 1


@pytest.mark.parametrize("cap, expect_len", [(10, 10), (0, 1500)])
def test_msg_max_chars_caps_each_body(cap, expect_len):
    adapter = _history_adapter(msg_max_chars=cap)
    long_body = "x" * 1500
    adapter._client.im.v1.message.list = Mock(return_value=_ok_response([_item("om_1", long_body)]))
    block = _run_fetch(adapter)
    body = block.split("\n")[1].rsplit(": ", 1)[1]
    assert len(body) == expect_len
    if cap:
        assert body.endswith("...")


# ---------------------------------------------------------------------------
# Inbound gate in _process_inbound_message
# ---------------------------------------------------------------------------


def _inbound_adapter(*, enabled=True, block=BLOCK):
    adapter = FeishuAdapter.__new__(FeishuAdapter)
    adapter._bot_open_id = "ou_bot"
    adapter._bot_user_id = ""
    adapter._bot_name = "Hermes"
    adapter._client = Mock()
    adapter._group_history_enabled = enabled
    adapter._download_feishu_message_resources = AsyncMock(return_value=([], []))
    adapter._fetch_message_text = AsyncMock(return_value=None)
    adapter._fetch_group_history_block = AsyncMock(return_value=block)
    adapter.get_chat_info = AsyncMock(return_value={"name": "Test Chat"})
    adapter._resolve_sender_profile = AsyncMock(
        return_value={"user_id": "u1", "user_name": "Alice", "user_id_alt": None}
    )
    adapter._resolve_source_chat_type = Mock(return_value="group")
    adapter.build_source = Mock(return_value=SimpleNamespace(thread_id=None))
    adapter._session_exists_for_source = Mock(return_value=False)
    adapter._reply_thread_enabled = Mock(return_value=False)
    adapter._resolve_channel_prompt = Mock(return_value=None)
    adapter._maybe_route_delegate_interaction_message = AsyncMock(return_value=False)
    adapter._maybe_route_delegate_foreground_message = AsyncMock(return_value=False)
    adapter._dispatch_inbound_event = AsyncMock()
    return adapter


def _message(text="hello", *, thread_id=None, root_id=None, mentions=None):
    return SimpleNamespace(
        content=json.dumps({"text": text}),
        message_type="text",
        message_id="om_now",
        mentions=mentions or [],
        chat_id="oc_g",
        parent_id=None,
        upper_message_id=None,
        thread_id=thread_id,
        root_id=root_id,
    )


def _run_inbound(adapter, message, *, chat_type="group"):
    asyncio.run(
        adapter._process_inbound_message(
            data=message,
            message=message,
            sender_id=None,
            chat_type=chat_type,
            message_id="om_now",
        )
    )
    if adapter._dispatch_inbound_event.await_count == 0:
        return None
    return adapter._dispatch_inbound_event.await_args.args[0]


def test_new_group_session_carries_block_on_metadata_not_text():
    adapter = _inbound_adapter()
    event = _run_inbound(adapter, _message("do the thing"))

    adapter._fetch_group_history_block.assert_awaited_once_with(
        chat_id="oc_g", exclude_message_id="om_now", thread_id=None
    )
    assert event.text == "do the thing"
    assert event.metadata[_FEISHU_GROUP_HISTORY_METADATA_KEY] == BLOCK


def test_topic_message_passes_its_real_thread_id_to_the_fetch():
    adapter = _inbound_adapter()
    _run_inbound(adapter, _message("inside a topic", thread_id="omt_topic", root_id="om_root"))
    adapter._fetch_group_history_block.assert_awaited_once_with(
        chat_id="oc_g", exclude_message_id="om_now", thread_id="omt_topic"
    )


def test_auto_thread_root_passes_no_thread_id_to_the_fetch():
    """The om_* auto-thread anchor is a message id, not a listable topic container."""
    adapter = _inbound_adapter()
    adapter._reply_thread_enabled = Mock(return_value=True)
    adapter._mark_auto_thread_pending = Mock()
    _run_inbound(adapter, _message("hello"))
    assert adapter._fetch_group_history_block.await_args.kwargs["thread_id"] is None


def test_mention_hint_and_text_are_untouched_by_the_block():
    adapter = _inbound_adapter()
    bob = SimpleNamespace(key="@_user_1", id=SimpleNamespace(open_id="ou_bob", user_id=""), name="Bob")
    event = _run_inbound(adapter, _message("@_user_1 please look", mentions=[bob]))

    assert event.text.startswith("[Mentioned:")
    assert "<group_messages>" not in event.text
    assert event.metadata[_FEISHU_GROUP_HISTORY_METADATA_KEY] == BLOCK


@pytest.mark.parametrize(
    "kwargs",
    [
        {"thread_id": "omt_topic", "root_id": "om_root"},
        {"root_id": "om_root"},
        {"thread_id": "omt_topic"},
    ],
)
def test_reply_and_topic_messages_inject_when_session_is_new(kwargs):
    """Feishu stamps thread ids on quote-reply chains, so thread flags are not the gate."""
    adapter = _inbound_adapter()
    event = _run_inbound(adapter, _message("follow-up", **kwargs))
    adapter._fetch_group_history_block.assert_awaited_once()
    assert event.text == "follow-up"
    assert event.metadata[_FEISHU_GROUP_HISTORY_METADATA_KEY] == BLOCK


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"thread_id": "omt_topic", "root_id": "om_root"},
        {"root_id": "om_root"},
    ],
)
def test_existing_session_skips_injection(kwargs):
    adapter = _inbound_adapter()
    adapter._session_exists_for_source = Mock(return_value=True)
    event = _run_inbound(adapter, _message("follow-up", **kwargs))
    # The auto-thread branch may consult the same check for topic messages,
    # so assert it was used rather than counting calls.
    assert adapter._session_exists_for_source.called
    adapter._fetch_group_history_block.assert_not_awaited()
    assert event.text == "follow-up"
    assert _FEISHU_GROUP_HISTORY_METADATA_KEY not in event.metadata


def test_session_check_sees_the_final_source():
    """The gate must evaluate the source the gateway will key the session on."""
    adapter = _inbound_adapter()
    final_source = SimpleNamespace(thread_id=None)
    adapter.build_source = Mock(return_value=final_source)
    _run_inbound(adapter, _message("hello"))
    assert adapter._session_exists_for_source.call_args.args[0] is final_source


def test_dm_messages_are_not_injected():
    adapter = _inbound_adapter()
    adapter._resolve_source_chat_type = Mock(return_value="dm")
    event = _run_inbound(adapter, _message("hi"), chat_type="p2p")
    adapter._fetch_group_history_block.assert_not_awaited()
    assert event.text == "hi"


def test_commands_are_not_injected():
    adapter = _inbound_adapter()
    event = _run_inbound(adapter, _message("/reset"))
    adapter._fetch_group_history_block.assert_not_awaited()
    assert event.text == "/reset"
    assert event.message_type == MessageType.COMMAND


def test_disabled_feature_never_fetches():
    adapter = _inbound_adapter(enabled=False)
    event = _run_inbound(adapter, _message("hello"))
    adapter._fetch_group_history_block.assert_not_awaited()
    assert event.text == "hello"


def test_missing_client_never_fetches():
    adapter = _inbound_adapter()
    adapter._client = None
    event = _run_inbound(adapter, _message("hello"))
    adapter._fetch_group_history_block.assert_not_awaited()
    assert event.text == "hello"


def test_empty_history_leaves_text_untouched():
    adapter = _inbound_adapter(block="")
    event = _run_inbound(adapter, _message("hello"))
    adapter._fetch_group_history_block.assert_awaited_once()
    assert event.text == "hello"
    assert _FEISHU_GROUP_HISTORY_METADATA_KEY not in event.metadata


def test_auto_threading_still_injects_for_root_message():
    """Auto-threading rewrites thread_id to the om_* root; the gate must use the original ids."""
    adapter = _inbound_adapter()
    adapter._reply_thread_enabled = Mock(return_value=True)
    adapter._mark_auto_thread_pending = Mock()
    event = _run_inbound(adapter, _message("hello"))
    adapter._fetch_group_history_block.assert_awaited_once()
    assert event.metadata[_FEISHU_GROUP_HISTORY_METADATA_KEY] == BLOCK


def test_legacy_adapter_without_settings_attribute_is_unaffected():
    adapter = _inbound_adapter()
    del adapter._group_history_enabled
    event = _run_inbound(adapter, _message("hello"))
    adapter._fetch_group_history_block.assert_not_awaited()
    assert event.text == "hello"


# ---------------------------------------------------------------------------
# Batching keeps one block per turn
# ---------------------------------------------------------------------------


def _clear_feishu_env(monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith("FEISHU_") or name.startswith("HERMES_FEISHU_"):
            monkeypatch.delenv(name, raising=False)


def _event_with_block(text, message_id, source, **kwargs):
    event = MessageEvent(text=text, message_type=MessageType.TEXT, source=source, message_id=message_id, **kwargs)
    event.metadata[_FEISHU_GROUP_HISTORY_METADATA_KEY] = BLOCK
    return event


def test_text_batch_keeps_first_events_block_and_plain_merged_text(monkeypatch):
    _clear_feishu_env(monkeypatch)
    adapter = FeishuAdapter(PlatformConfig())
    adapter.handle_message = AsyncMock()
    source = SessionSource(
        platform=adapter.platform, chat_id="oc_g", chat_name="G", chat_type="group",
        user_id="ou_user", user_name="Alice",
    )

    async def _sleep(_delay):
        return None

    async def _run():
        with patch("plugins.platforms.feishu.adapter.asyncio.sleep", side_effect=_sleep):
            await adapter._dispatch_inbound_event(_event_with_block("first", "om_1", source))
            await adapter._dispatch_inbound_event(_event_with_block("second", "om_2", source))
            await asyncio.gather(*adapter._pending_text_batch_tasks.values(), return_exceptions=True)

    asyncio.run(_run())

    assert adapter.handle_message.await_count == 1
    merged = adapter.handle_message.await_args.args[0]
    assert merged.text == "first\nsecond"
    assert "<group_messages>" not in merged.text
    assert merged.metadata[_FEISHU_GROUP_HISTORY_METADATA_KEY] == BLOCK


def test_media_batch_keeps_first_events_block_and_plain_caption(monkeypatch):
    _clear_feishu_env(monkeypatch)
    adapter = FeishuAdapter(PlatformConfig())
    adapter.handle_message = AsyncMock()
    source = SessionSource(
        platform=adapter.platform, chat_id="oc_g", chat_name="G", chat_type="group",
        user_id="ou_user", user_name="Alice",
    )

    def _photo(text, message_id, url):
        event = MessageEvent(
            text=text, message_type=MessageType.PHOTO, source=source, message_id=message_id,
            media_urls=[url], media_types=["image/png"],
        )
        event.metadata[_FEISHU_GROUP_HISTORY_METADATA_KEY] = BLOCK
        return event

    async def _sleep(_delay):
        return None

    async def _run():
        with patch("plugins.platforms.feishu.adapter.asyncio.sleep", side_effect=_sleep):
            await adapter._dispatch_inbound_event(_photo("cap one", "om_1", "/tmp/a.png"))
            await adapter._dispatch_inbound_event(_photo("cap two", "om_2", "/tmp/b.png"))
            await asyncio.gather(*adapter._pending_media_batch_tasks.values(), return_exceptions=True)

    asyncio.run(_run())

    assert adapter.handle_message.await_count == 1
    merged = adapter.handle_message.await_args.args[0]
    assert "<group_messages>" not in merged.text
    assert "cap one" in merged.text and "cap two" in merged.text
    assert merged.metadata[_FEISHU_GROUP_HISTORY_METADATA_KEY] == BLOCK


# ---------------------------------------------------------------------------
# Gateway places the block under <source>, above every other block
# ---------------------------------------------------------------------------


def _runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig()
    runner.adapters = {}
    runner._model = "openai/gpt-4.1-mini"
    runner._base_url = None
    return runner


SOURCE_HEADER = '<source>{"platform":"feishu","channel":"oc_g","uid":"ou_user","uname":"Ada"}</source>'


@pytest.mark.asyncio
async def test_gateway_places_block_under_source_and_above_sender_prefix_and_reply():
    """Shared thread session: [Ada] prefix and the reply quote keep their shape below the block."""
    runner = _runner()
    source = SessionSource(
        platform=Platform.FEISHU, chat_id="oc_g", chat_type="group", user_id="ou_user", user_name="Ada",
        thread_id="om_auto_root",
    )
    event = MessageEvent(
        text="继续处理", source=source, reply_to_message_id="om_parent", reply_to_text="上一条消息",
    )
    event.metadata[_FEISHU_GROUP_HISTORY_METADATA_KEY] = BLOCK

    result = await runner._prepare_inbound_message_text(event=event, source=source, history=[])

    assert result == (
        f"{SOURCE_HEADER}\n\n"
        f"{BLOCK}\n\n"
        '[Replying to: "上一条消息"]\n\n'
        "[Ada] 继续处理"
    )


@pytest.mark.asyncio
async def test_gateway_places_block_under_source_for_plain_group_message():
    runner = _runner()
    source = SessionSource(platform=Platform.FEISHU, chat_id="oc_g", chat_type="group", user_id="ou_user", user_name="Ada")
    event = MessageEvent(text="继续处理", source=source)
    event.metadata[_FEISHU_GROUP_HISTORY_METADATA_KEY] = BLOCK

    result = await runner._prepare_inbound_message_text(event=event, source=source, history=[])

    assert result == f"{SOURCE_HEADER}\n\n{BLOCK}\n\n继续处理"


@pytest.mark.asyncio
async def test_gateway_output_is_unchanged_without_block():
    runner = _runner()
    source = SessionSource(
        platform=Platform.FEISHU, chat_id="oc_g", chat_type="group", user_id="ou_user", user_name="Ada",
        thread_id="om_auto_root",
    )
    event = MessageEvent(text="继续处理", source=source)

    result = await runner._prepare_inbound_message_text(event=event, source=source, history=[])

    assert result == f"{SOURCE_HEADER}\n\n[Ada] 继续处理"


@pytest.mark.asyncio
async def test_gateway_ignores_blank_or_non_string_block():
    runner = _runner()
    source = SessionSource(platform=Platform.FEISHU, chat_id="oc_g", chat_type="group", user_id="ou_user", user_name="Ada")
    for bad in ("", "   ", None):
        event = MessageEvent(text="hi", source=source)
        event.metadata[_FEISHU_GROUP_HISTORY_METADATA_KEY] = bad
        result = await runner._prepare_inbound_message_text(event=event, source=source, history=[])
        assert result == f"{SOURCE_HEADER}\n\nhi"
