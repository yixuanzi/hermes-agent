import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.event import MessageEvent
from gateway.run import GatewayRunner
from gateway.session import SessionSource


def _make_runner(config: GatewayConfig) -> GatewayRunner:
    runner = object.__new__(GatewayRunner)
    runner.config = config
    runner.adapters = {}
    runner._model = "openai/gpt-4.1-mini"
    runner._base_url = None
    return runner


@pytest.mark.asyncio
@pytest.mark.parametrize("thread_id", ["171.000", "170.000"])
async def test_preprocess_includes_slack_source_and_author_mention_for_shared_thread(
    thread_id: str,
):
    """Shared Slack threads expose the current author's verifiable user ID
    next to the display name so 'mention me again' requests can bind the
    mention to the CURRENT speaker (#17916)."""
    runner = _make_runner(
        GatewayConfig(
            platforms={
                Platform.SLACK: PlatformConfig(enabled=True, token="fake"),
            },
        )
    )
    source = SessionSource(
        platform=Platform.SLACK,
        chat_id="C123",
        chat_name="team-channel",
        chat_type="group",
        user_id="U123",
        user_name="Alice",
        thread_id=thread_id,
    )
    event = MessageEvent(text="mention me again", source=source)

    result = await runner._prepare_inbound_message_text(
        event=event,
        source=source,
        history=[],
    )

    assert result == (
        '<source>{"platform":"slack","channel":"C123",'
        '"uid":"U123","uname":"Alice"}</source>\n\n'
        "[Alice | Slack user <@U123>] mention me again"
    )


@pytest.mark.asyncio
async def test_preprocess_does_not_build_slack_source_without_user_id():
    runner = _make_runner(GatewayConfig())
    source = SessionSource(
        platform=Platform.SLACK,
        chat_id="C123",
        chat_type="group",
        user_name="Alice",
        thread_id="171.000",
    )
    event = MessageEvent(text="hello", source=source)

    result = await runner._prepare_inbound_message_text(
        event=event,
        source=source,
        history=[],
    )

    assert result == "[Alice] hello"


@pytest.mark.asyncio
async def test_preprocess_keeps_plain_text_for_default_group_sessions():
    runner = _make_runner(
        GatewayConfig(
            platforms={
                Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake"),
            },
        )
    )
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1002285219667",
        chat_name="Test Group",
        chat_type="group",
        user_name="Alice",
    )
    event = MessageEvent(text="hello", source=source)

    result = await runner._prepare_inbound_message_text(
        event=event,
        source=source,
        history=[],
    )

    assert result == "hello"


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", [Platform.SLACK, Platform.FEISHU])
@pytest.mark.parametrize("chat_type", ["dm", "group"])
async def test_preprocess_adds_structured_source_prefix_for_messaging_platforms(
    platform: Platform, chat_type: str,
):
    runner = _make_runner(GatewayConfig())
    source = SessionSource(
        platform=platform,
        chat_id="C123",
        chat_type=chat_type,
        user_id="U456",
        user_name="Alice",
    )
    event = MessageEvent(text="hello", source=source)

    result = await runner._prepare_inbound_message_text(
        event=event,
        source=source,
        history=[],
    )

    assert result == (
        f'<source>{{"platform":"{platform.value}","channel":"C123",'
        '"uid":"U456","uname":"Alice"}</source>\n\nhello'
    )


@pytest.mark.asyncio
async def test_preprocess_keeps_slack_source_prefix_before_thread_reply_context():
    runner = _make_runner(GatewayConfig())
    source = SessionSource(
        platform=Platform.SLACK,
        chat_id="C02MVR0PADS",
        chat_type="group",
        user_id="U02LQJ2S5HN",
        user_name="Guisheng(郭桂生)",
    )
    event = MessageEvent(
        text="获取 TEST_KEY 内容",
        source=source,
        reply_to_message_id="1710000000.000001",
        reply_to_text="当前 userenv 数据，返回对应 key",
    )

    result = await runner._prepare_inbound_message_text(
        event=event,
        source=source,
        history=[],
    )

    assert result == (
        '<source>{"platform":"slack","channel":"C02MVR0PADS",'
        '"uid":"U02LQJ2S5HN","uname":"Guisheng(郭桂生)"}</source>\n\n'
        '[Replying to: "当前 userenv 数据，返回对应 key"]\n\n获取 TEST_KEY 内容'
    )


@pytest.mark.asyncio
async def test_preprocess_keeps_feishu_source_prefix_before_thread_reply_context():
    runner = _make_runner(GatewayConfig())
    source = SessionSource(
        platform=Platform.FEISHU,
        chat_id="oc_feishu",
        chat_type="group",
        user_id="ou_user",
        user_name="Ada",
    )
    event = MessageEvent(
        text="继续处理",
        source=source,
        reply_to_message_id="om_parent",
        reply_to_text="上一条消息",
    )

    result = await runner._prepare_inbound_message_text(
        event=event,
        source=source,
        history=[],
    )

    assert result == (
        '<source>{"platform":"feishu","channel":"oc_feishu",'
        '"uid":"ou_user","uname":"Ada"}</source>\n\n'
        '[Replying to: "上一条消息"]\n\n继续处理'
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("thread_id", ["om_auto_root", "omt_existing"])
async def test_preprocess_adds_feishu_source_prefix_to_shared_threads(thread_id: str):
    """Feishu thread routing must not suppress the structured source header."""
    runner = _make_runner(GatewayConfig())
    source = SessionSource(
        platform=Platform.FEISHU,
        chat_id="oc_feishu",
        chat_type="group",
        user_id="ou_user",
        user_name="Alice",
        thread_id=thread_id,
    )
    event = MessageEvent(text="hello", source=source)

    result = await runner._prepare_inbound_message_text(
        event=event,
        source=source,
        history=[],
    )

    assert result == (
        '<source>{"platform":"feishu","channel":"oc_feishu",'
        '"uid":"ou_user","uname":"Alice"}</source>\n\n'
        '[Alice] hello'
    )


@pytest.mark.asyncio
async def test_preprocess_keeps_empty_uname_in_feishu_source_prefix():
    runner = _make_runner(GatewayConfig())
    source = SessionSource(
        platform=Platform.FEISHU,
        chat_id="oc_feishu",
        chat_type="dm",
        user_id="ou_user",
        user_name=None,
    )
    event = MessageEvent(text="hello", source=source)

    result = await runner._prepare_inbound_message_text(
        event=event,
        source=source,
        history=[],
    )

    assert result == (
        '<source>{"platform":"feishu","channel":"oc_feishu",'
        '"uid":"ou_user","uname":""}</source>\n\nhello'
    )
