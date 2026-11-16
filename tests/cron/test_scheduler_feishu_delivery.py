"""Cron delivery into Feishu topics: origin-thread inheritance and post-send bookkeeping."""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future
from unittest.mock import AsyncMock, MagicMock, patch

from cron.scheduler import _deliver_result, _resolve_delivery_target


def _explicit_target(deliver: str, origin: dict) -> dict:
    with patch("gateway.channel_directory.resolve_channel_name", return_value=None):
        return _resolve_delivery_target({"deliver": deliver, "origin": origin})


def test_an_explicit_target_on_the_origin_chat_lands_in_the_origin_topic():
    origin = {"platform": "feishu", "chat_id": "oc_abc", "thread_id": "omt_111"}
    target = _explicit_target("feishu:oc_abc", origin)
    assert (target["chat_id"], target["thread_id"]) == ("oc_abc", "omt_111")


def test_an_explicit_target_on_another_chat_does_not_inherit_the_topic():
    origin = {"platform": "feishu", "chat_id": "oc_abc", "thread_id": "omt_111"}
    target = _explicit_target("feishu:oc_other", origin)
    assert (target["chat_id"], target["thread_id"]) == ("oc_other", None)


class _SdkResponse:
    """A raw lark SDK response (e.g. ReplyMessageResponse): attributes, but no ``.get``."""


def _deliver_threaded(raw_response, deliver="feishu:oc_123:omt_456", origin=None):
    from gateway.config import Platform
    from gateway.platforms.base import SendResult

    adapter = AsyncMock()
    adapter.send.return_value = SendResult(success=True, message_id="om_1", raw_response=raw_response)
    pconfig = MagicMock()
    pconfig.enabled = True
    config = MagicMock()
    config.platforms = {Platform.FEISHU: pconfig}
    loop = MagicMock()
    loop.is_running.return_value = True

    def run_now(coro, _loop):
        future = Future()
        try:
            future.set_result(asyncio.run(coro))
        except BaseException as exc:  # health: allow BLE001 -- mirrors run_coroutine_threadsafe's future
            future.set_exception(exc)
        return future

    job = {"id": "feishu-thread-job", "deliver": deliver,
           "origin": origin or {"platform": "feishu", "chat_id": "oc_123"}}
    with patch("gateway.config.load_gateway_config", return_value=config), \
         patch("cron.scheduler.load_config", return_value={"cron": {"wrap_response": False}}), \
         patch("asyncio.run_coroutine_threadsafe", side_effect=run_now):
        error = _deliver_result(job, "threaded output", adapters={Platform.FEISHU: adapter}, loop=loop)
    return error, adapter


def test_an_sdk_object_response_in_a_topic_is_delivered_once_without_error():
    error, adapter = _deliver_threaded(_SdkResponse())
    assert error is None
    assert adapter.send.await_count == 1


def test_a_dict_response_still_reports_a_thread_fallback(caplog):
    with caplog.at_level(logging.WARNING):
        _error, adapter = _deliver_threaded({"thread_fallback": True, "requested_thread_id": "omt_456"})
    assert adapter.send.await_count == 1
    assert any("was not found; delivered without thread_id" in r.getMessage() for r in caplog.records)


def test_no_lost_thread_warning_when_delivering_to_another_chat(caplog):
    origin = {"platform": "feishu", "chat_id": "oc_origin", "thread_id": "omt_9"}
    with caplog.at_level(logging.WARNING):
        _deliver_threaded({"message_id": "om_9"}, deliver="feishu:oc_123", origin=origin)
    assert not any("delivery target lost it" in r.getMessage() for r in caplog.records)
