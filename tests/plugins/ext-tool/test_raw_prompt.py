"""ext-tools raw_prompt：/codex、/claude 模式下只把用户原文交给后端。"""

import asyncio
import contextvars
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "plugins" / "ext-tools"))

import codex_cmd  # noqa: E402
import raw_prompt  # noqa: E402
from gateway.config import GatewayConfig, Platform, PlatformConfig  # noqa: E402
from gateway.platforms.base import MessageEvent, MessageType  # noqa: E402
from gateway.run import GatewayRunner  # noqa: E402
from gateway.session import SessionSource  # noqa: E402


class FakeBackendSession:
    def __init__(self):
        self.inputs = []

    def run_turn(self, user_input, *, turn_timeout=600.0):
        self.inputs.append(user_input)
        return "ok"


@pytest.fixture(autouse=True)
def _clean_context():
    raw_prompt._inbound.set(None)
    yield
    raw_prompt._inbound.set(None)


def _runner(*, shared=False, image_mode="native"):
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.FEISHU: PlatformConfig(enabled=True)},
        group_sessions_per_user=not shared,
    )
    runner.adapters = {}
    runner._decide_image_input_mode = lambda **_: image_mode
    return runner


def _source():
    return SessionSource(
        platform=Platform.FEISHU,
        chat_id="oc_group",
        chat_type="group",
        user_id="ou_alice",
        user_name="Alice",
    )


def _event(text, **kwargs):
    return MessageEvent(text=text, source=_source(), **kwargs)


def _session():
    session = FakeBackendSession()
    raw_prompt.install_session_passthrough(session)
    return session


async def _gateway_turn(runner, session, event, *, api_message=None):
    """与网关一致：先加工入站消息，再在复制了 context 的线程里跑本轮。"""
    prepared = await runner._prepare_profile_scoped_inbound_message_text(
        event=event, source=event.source, history=[], session_key="feishu:oc_group"
    )
    user_input = api_message(prepared) if api_message else prepared
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None, contextvars.copy_context().run, lambda: session.run_turn(user_input=user_input)
    )
    return prepared


def test_text_message_reaches_backend_verbatim():
    runner, session = _runner(), _session()
    codex_cmd.on_pre_gateway_dispatch(event=_event("hi"), gateway=runner)
    text = "  把 README 里的安装步骤\n改成 uv 的写法  "
    event = _event(
        text,
        reply_to_message_id="om_prev",
        reply_to_text="上一条回复",
        metadata={"group_history_block": "[群聊历史]\nBob: 早"},
    )

    prepared = asyncio.run(
        _gateway_turn(
            runner, session, event,
            api_message=lambda p: f"[System note: model switched]\n\n{p}",
        )
    )

    assert session.inputs == [text]
    # Hermes 自己的记录照旧带网关上下文
    assert prepared.startswith("<source>") and "[群聊历史]" in prepared
    assert '[Replying to: "上一条回复"]' in prepared


def test_attachment_keeps_notes_and_strips_gateway_context(tmp_path):
    runner, session = _runner(shared=True), _session()
    raw_prompt.install_gateway_capture(runner)
    doc = tmp_path / "doc_abc123_report.pdf"
    doc.write_bytes(b"%PDF-1.4")
    event = _event(
        "看看这个文件",
        message_type=MessageType.DOCUMENT,
        media_urls=[str(doc)],
        media_types=["application/pdf"],
        reply_to_message_id="om_prev",
        reply_to_text="之前的讨论",
        reply_to_is_own_message=True,
        channel_context="[频道上下文]",
        metadata={"group_history_block": "[群聊历史]\nBob: 早"},
    )

    prepared = asyncio.run(_gateway_turn(runner, session, event))

    assert "[Alice] 看看这个文件" in prepared  # 共享会话：网关确实加了昵称前缀
    [sent] = session.inputs
    assert str(doc) in sent and sent.endswith("\n\n看看这个文件")
    for gateway_only in ("<source>", "[群聊历史]", "Replying to", "[频道上下文]", "[New message]", "[Alice]"):
        assert gateway_only not in sent


def test_native_image_keeps_image_parts():
    runner, session = _runner(), _session()
    raw_prompt.install_gateway_capture(runner)
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}

    def native(prepared):
        return [{"type": "text", "text": prepared}, image]

    def image_event(caption):
        return _event(
            caption, message_type=MessageType.PHOTO,
            media_urls=["/tmp/a.png"], media_types=["image/png"],
        )

    asyncio.run(_gateway_turn(runner, session, image_event("这张图"), api_message=native))
    asyncio.run(_gateway_turn(runner, session, image_event(""), api_message=native))

    assert session.inputs == [[{"type": "text", "text": "这张图"}, image], [image]]


def test_queued_followup_gets_its_own_text_once():
    runner, session = _runner(), _session()
    raw_prompt.install_gateway_capture(runner)

    async def queued():
        await _gateway_turn(runner, session, _event("first"))
        # 排队的后续消息：同一协程里再次加工后递归跑下一轮
        await _gateway_turn(runner, session, _event("second"))
        # 没有经过加工的后续轮（无 pending event）：不能把上一条原文再发一遍
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, contextvars.copy_context().run,
            lambda: session.run_turn(user_input="queued text without event"),
        )

    asyncio.run(queued())

    assert session.inputs == ["first", "second", "queued text without event"]


def test_unrelated_or_blank_input_passes_through():
    session = _session()
    event = _event("hello")
    raw_prompt._inbound.set(raw_prompt._Inbound(event, event.source, "<source>{}</source>\n\nhello"))

    # 本轮输入不是由这条消息加工来的（如非开启者的 Hermes 轮之后）
    session.run_turn(user_input="something else")
    session.run_turn(user_input="<source>{}</source>\n\nhello")
    session.run_turn(user_input="<source>{}</source>\n\nhello")  # 只替换一次
    assert session.inputs == ["something else", "hello", "<source>{}</source>\n\nhello"]

    # 空文本（重启后的自动续跑轮）保留 Hermes 的续跑提示
    blank = _event("")
    raw_prompt._inbound.set(raw_prompt._Inbound(blank, blank.source, "<source>{}</source>\n\n"))
    note = "[System note: resume]\n\n<source>{}</source>\n\n"
    session.run_turn(user_input=note)
    assert session.inputs[-1] == note


def test_cli_turn_without_gateway_capture_is_untouched():
    session = _session()
    session.run_turn(user_input="cli text", turn_timeout=5.0)
    assert session.inputs == ["cli text"]


def test_capture_and_passthrough_install_once():
    runner, session = _runner(), _session()
    raw_prompt.install_gateway_capture(runner)
    wrapped = runner._prepare_inbound_message_text
    codex_cmd.on_pre_gateway_dispatch(event=_event("hi"), gateway=runner)
    assert runner._prepare_inbound_message_text is wrapped

    run_turn = session.run_turn
    raw_prompt.install_session_passthrough(session)
    assert session.run_turn is run_turn
