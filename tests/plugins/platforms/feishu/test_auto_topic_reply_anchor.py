"""Feishu turns keyed on an ``om_*`` root are answered under their own message.

The Feishu adapter pins this before the base pipeline; ``gateway/platforms/base.py`` keeps upstream's
rule (a topic message is answered under the message it quotes).
"""

import asyncio
from unittest.mock import patch

import pytest

from gateway.config import Platform
from gateway.platforms.base import BasePlatformAdapter, _reply_anchor_for_event
from gateway.platforms.event import MessageEvent
from gateway.session import SessionSource


def _anchor_after_feishu_ingress(*, thread_id, message_id="om_msg", reply_to="om_quoted", **event_fields):
    from plugins.platforms.feishu.adapter import FeishuAdapter

    seen = []

    async def base_handle_message(self, event):
        seen.append(event)

    source = SessionSource(platform=Platform.FEISHU, chat_id="oc_1", chat_type="group", thread_id=thread_id)
    event = MessageEvent(text="hi", source=source, message_id=message_id, reply_to_message_id=reply_to,
                         **event_fields)
    with patch.object(BasePlatformAdapter, "handle_message", base_handle_message):
        asyncio.run(FeishuAdapter.__new__(FeishuAdapter).handle_message(event))
    [handled] = seen
    return _reply_anchor_for_event(handled)


@pytest.mark.parametrize(
    "case",
    ["quote reply opening a topic", "message in an automatic topic's root-keyed session"],
)
def test_turn_keyed_on_an_om_root_answers_under_its_own_message(case):
    thread_id = "om_msg" if case == "quote reply opening a topic" else "om_root"
    assert _anchor_after_feishu_ingress(thread_id=thread_id) == "om_msg"


def test_real_topic_keeps_upstreams_quoted_message_anchor():
    assert _anchor_after_feishu_ingress(thread_id="omt_topic") == "om_quoted"


def test_internal_events_and_existing_overrides_are_left_alone():
    assert _anchor_after_feishu_ingress(thread_id="om_root", internal=True) is None
    assert _anchor_after_feishu_ingress(thread_id="om_root", reply_anchor_override="om_redirect") == "om_redirect"
