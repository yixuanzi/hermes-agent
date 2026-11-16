"""Tests for the Feishu gateway integration."""

import asyncio
import json
import os
import socket
import tempfile
import time
import unittest
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock, patch

import pytest

from gateway.platforms.event import ProcessingOutcome


if TYPE_CHECKING:
    from plugins.platforms.feishu.adapter import FeishuAdapter

try:
    import lark_oapi
    _HAS_LARK_OAPI = True
except ImportError:
    _HAS_LARK_OAPI = False

class _FakeRequestContent:
    def __init__(self, body: bytes):
        self.body = body
        self.read_sizes: list[int] = []

    async def readexactly(self, size: int) -> bytes:
        self.read_sizes.append(size)
        if len(self.body) < size:
            raise asyncio.IncompleteReadError(self.body, size)
        return self.body[:size]

def _mock_event_dispatcher_builder(mock_handler_class):
    mock_builder = Mock()
    mock_builder.register_p2_im_message_message_read_v1 = Mock(return_value=mock_builder)
    mock_builder.register_p2_im_message_receive_v1 = Mock(return_value=mock_builder)
    mock_builder.register_p2_im_message_reaction_created_v1 = Mock(return_value=mock_builder)
    mock_builder.register_p2_im_message_reaction_deleted_v1 = Mock(return_value=mock_builder)
    mock_builder.register_p2_card_action_trigger = Mock(return_value=mock_builder)
    mock_builder.build = Mock(return_value=object())
    mock_handler_class.builder = Mock(return_value=mock_builder)
    return mock_builder

class TestConfigEnvOverrides(unittest.TestCase):
    @patch.dict(os.environ, {
        "FEISHU_APP_ID": "cli_xxx",
        "FEISHU_APP_SECRET": "secret_xxx",
        "FEISHU_CONNECTION_MODE": "websocket",
        "FEISHU_DOMAIN": "feishu",
    }, clear=False)
    def test_feishu_config_loaded_from_env(self):
        from gateway.config import GatewayConfig, Platform, _apply_env_overrides

        config = GatewayConfig()
        _apply_env_overrides(config)

        self.assertIn(Platform.FEISHU, config.platforms)
        self.assertTrue(config.platforms[Platform.FEISHU].enabled)
        self.assertEqual(config.platforms[Platform.FEISHU].extra["app_id"], "cli_xxx")
        self.assertEqual(config.platforms[Platform.FEISHU].extra["connection_mode"], "websocket")

    def test_reply_thread_flag_defaults_true_and_parses_false_values(self):
        from plugins.platforms.feishu.adapter_aegis import load_aegis_settings

        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(load_aegis_settings({}).reply_thread)
        for value in ("false", "0", "no", "off", " FALSE "):
            with self.subTest(value=value), patch.dict(
                os.environ, {"FEISHU_REPLY_THREAD": value}, clear=True
            ):
                self.assertFalse(load_aegis_settings({}).reply_thread)

    def test_reply_thread_flag_invalid_value_warns_and_defaults_true(self):
        from plugins.platforms.feishu.adapter_aegis import load_aegis_settings

        with patch.dict(
            os.environ, {"FEISHU_REPLY_THREAD": "sometimes"}, clear=True
        ), self.assertLogs(level="WARNING") as logs:
            self.assertTrue(load_aegis_settings({}).reply_thread)
        self.assertTrue(any("FEISHU_REPLY_THREAD" in line for line in logs.output))

class TestFeishuMessageNormalization(unittest.TestCase):

    def test_normalize_interactive_card_preserves_title_body_and_actions(self):
        from plugins.platforms.feishu.adapter import normalize_feishu_message

        normalized = normalize_feishu_message(
            message_type="interactive",
            raw_content=json.dumps(
                {
                    "card": {
                        "header": {"title": {"tag": "plain_text", "content": "Build Failed"}},
                        "elements": [
                            {"tag": "div", "text": {"tag": "lark_md", "content": "Service: payments-api"}},
                            {"tag": "div", "text": {"tag": "plain_text", "content": "Branch: main"}},
                            {
                                "tag": "action",
                                "actions": [
                                    {"tag": "button", "text": {"tag": "plain_text", "content": "View Logs"}},
                                    {"tag": "button", "text": {"tag": "plain_text", "content": "Retry"}},
                                ],
                            },
                        ],
                    }
                }
            ),
        )

        self.assertEqual(normalized.relation_kind, "interactive")
        self.assertEqual(
            normalized.text_content,
            "Build Failed\nService: payments-api\nBranch: main\nView Logs\nRetry\nActions: View Logs, Retry",
        )

class TestFeishuAdapterMessaging(unittest.TestCase):

    def test_disconnect_sends_websocket_close_frame(self):
        """Regression test for #10202: disconnect() must call the WSS
        client's ``_disconnect()`` coroutine so a WebSocket CLOSE frame
        is sent to Feishu. Without this, Feishu's server continues
        routing to the stale connection, silencing the channel.
        """
        import threading
        from types import SimpleNamespace
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())

        # Real thread loop to schedule the close coroutine on.
        ws_thread_loop = asyncio.new_event_loop()
        ready = threading.Event()

        def _run_loop() -> None:
            asyncio.set_event_loop(ws_thread_loop)
            ready.set()
            ws_thread_loop.run_forever()

        thread = threading.Thread(target=_run_loop, daemon=True)
        thread.start()
        ready.wait()

        close_called = threading.Event()

        async def _fake_disconnect() -> None:
            close_called.set()

        ws_client = SimpleNamespace(_disconnect=_fake_disconnect, _auto_reconnect=True)
        adapter._ws_client = ws_client
        adapter._ws_thread_loop = ws_thread_loop
        adapter._ws_future = None

        try:
            asyncio.run(adapter.disconnect())
        finally:
            if not ws_thread_loop.is_closed():
                ws_thread_loop.call_soon_threadsafe(ws_thread_loop.stop)
            thread.join(timeout=2.0)
            if not ws_thread_loop.is_closed():
                ws_thread_loop.close()

        self.assertTrue(
            close_called.is_set(),
            "disconnect() must schedule ws_client._disconnect() on the ws thread loop",
        )
        # _disable_websocket_auto_reconnect() must still run.
        self.assertIsNone(adapter._ws_client)

    @patch.dict(os.environ, {
        "FEISHU_APP_ID": "cli_app",
        "FEISHU_APP_SECRET": "secret_app",
    }, clear=True)
    def test_connect_websocket_sets_channel_ua_tag_and_uses_owned_executor(self):
        """Verify the WebSocket client uses the channel tag and owned executor.

        Without this UA tag the Feishu server does not push group @mention
        events over the WebSocket transport. The long-lived client must also
        stay off asyncio's shared default executor. See
        https://github.com/NousResearch/hermes-agent/issues/50656
        https://github.com/NousResearch/hermes-agent/issues/78318
        """
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        owned_executor = object()
        submitted_executors = []

        with (
            patch("plugins.platforms.feishu.adapter.FEISHU_AVAILABLE", True),
            patch("plugins.platforms.feishu.adapter.FEISHU_WEBSOCKET_AVAILABLE", True),
            patch("plugins.platforms.feishu.adapter.lark",
                  SimpleNamespace(LogLevel=SimpleNamespace(INFO="INFO", WARNING="WARNING"))),
            patch("plugins.platforms.feishu.adapter.EventDispatcherHandler") as mock_handler_class,
            patch("plugins.platforms.feishu.adapter.FeishuWSClient") as mock_ws_client,
            patch("plugins.platforms.feishu.adapter._run_official_feishu_ws_client"),
            patch("plugins.platforms.feishu.adapter.acquire_scoped_lock", return_value=(True, None)),
            patch("plugins.platforms.feishu.adapter.release_scoped_lock"),
            patch.object(adapter, "_hydrate_bot_identity", new=AsyncMock()),
            patch.object(adapter, "_build_lark_client", return_value=SimpleNamespace()),
            patch.object(adapter, "_get_sdk_executor", return_value=owned_executor),
        ):
            _mock_event_dispatcher_builder(mock_handler_class)

            loop = asyncio.new_event_loop()
            future = loop.create_future()
            future.set_result(None)

            class _Loop:
                def run_in_executor(self, executor, *_args, **_kwargs):
                    submitted_executors.append(executor)
                    return future
                def is_closed(self):
                    return False

            try:
                with patch("plugins.platforms.feishu.adapter.asyncio.get_running_loop",
                           return_value=_Loop()):
                    connected = asyncio.run(adapter.connect())
            finally:
                loop.close()

        self.assertTrue(connected)
        # Verify the Channel SDK UA tag is present — this is the fix for
        # group @mention message delivery over WebSocket.
        mock_ws_client.assert_called_once()
        call_kwargs = mock_ws_client.call_args.kwargs
        self.assertIn("extra_ua_tags", call_kwargs,
                      "FeishuWSClient must receive extra_ua_tags for group @mention delivery")
        self.assertEqual(call_kwargs["extra_ua_tags"], ["channel"],
                         "extra_ua_tags must be ['channel'] to enable group event routing")
        self.assertEqual(submitted_executors, [owned_executor])

    @patch.dict(os.environ, {}, clear=True)
    def test_edit_message_falls_back_to_text_when_post_update_is_rejected(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        captured = {"calls": []}

        class _MessageAPI:
            def update(self, request):
                captured["calls"].append(request)
                if len(captured["calls"]) == 1:
                    return SimpleNamespace(success=lambda: False, code=230001, msg="content format of the post type is incorrect")
                return SimpleNamespace(success=lambda: True)

        adapter._client = SimpleNamespace(
            im=SimpleNamespace(
                v1=SimpleNamespace(
                    message=_MessageAPI(),
                )
            )
        )

        async def _direct(func, *args, **kwargs):
            return func(*args, **kwargs)

        with patch("plugins.platforms.feishu.adapter.asyncio.to_thread", side_effect=_direct):
            result = asyncio.run(
                adapter.edit_message(
                    chat_id="oc_chat",
                    message_id="om_progress",
                    content="可以用 **粗体** 和 *斜体*。",
                )
            )

        self.assertTrue(result.success)
        self.assertEqual(captured["calls"][0].request_body.msg_type, "post")
        self.assertEqual(captured["calls"][1].request_body.msg_type, "text")
        self.assertEqual(
            captured["calls"][1].request_body.content,
            json.dumps({"text": "可以用 粗体 和 斜体。"}, ensure_ascii=False),
        )

    @patch.dict(os.environ, {
        "FEISHU_APP_ID": "cli_app",
        "FEISHU_APP_SECRET": "secret_app",
    }, clear=True)
    def test_connect_acquires_scoped_lock_and_disconnect_releases_it(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        ws_client = SimpleNamespace()

        with (
            patch("plugins.platforms.feishu.adapter.FEISHU_AVAILABLE", True),
            patch("plugins.platforms.feishu.adapter.FEISHU_WEBSOCKET_AVAILABLE", True),
            patch("plugins.platforms.feishu.adapter.lark", SimpleNamespace(LogLevel=SimpleNamespace(INFO="INFO", WARNING="WARNING"))),
            patch("plugins.platforms.feishu.adapter.EventDispatcherHandler") as mock_handler_class,
            patch("plugins.platforms.feishu.adapter.FeishuWSClient", return_value=ws_client),
            patch("plugins.platforms.feishu.adapter._run_official_feishu_ws_client"),
            patch("plugins.platforms.feishu.adapter.acquire_scoped_lock", return_value=(True, None)) as acquire_lock,
            patch("plugins.platforms.feishu.adapter.release_scoped_lock") as release_lock,
            patch.object(adapter, "_hydrate_bot_identity", new=AsyncMock()),
            patch.object(adapter, "_build_lark_client", return_value=SimpleNamespace()),
        ):
            _mock_event_dispatcher_builder(mock_handler_class)

            loop = asyncio.new_event_loop()
            future = loop.create_future()
            future.set_result(None)

            class _Loop:
                def run_in_executor(self, *_args, **_kwargs):
                    return future

                def is_closed(self):
                    return False

            try:
                with patch("plugins.platforms.feishu.adapter.asyncio.get_running_loop", return_value=_Loop()):
                    connected = asyncio.run(adapter.connect())
                    asyncio.run(adapter.disconnect())
            finally:
                loop.close()

        self.assertTrue(connected)
        self.assertIsNone(adapter._event_handler)
        acquire_lock.assert_called_once_with(
            "feishu-app-id",
            "cli_app",
            metadata={"platform": "feishu"},
        )
        release_lock.assert_called_once_with("feishu-app-id", "cli_app")

    def test_build_websocket_client_omits_ua_tag_for_legacy_sdk(self):
        """Older lark-oapi clients must still connect without the new keyword."""
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        calls = []

        class _LegacyWSClient:
            def __init__(self, app_id, app_secret, log_level, event_handler, domain):
                calls.append({
                    "app_id": app_id,
                    "app_secret": app_secret,
                    "log_level": log_level,
                    "event_handler": event_handler,
                    "domain": domain,
                })

        with (
            patch("plugins.platforms.feishu.adapter.FeishuWSClient", _LegacyWSClient),
            patch("plugins.platforms.feishu.adapter.lark", SimpleNamespace(LogLevel=SimpleNamespace(INFO="INFO"))),
        ):
            client = adapter._build_websocket_client(domain="https://open.feishu.cn", event_handler="handler")

        self.assertIsInstance(client, _LegacyWSClient)
        self.assertEqual(calls, [{
            "app_id": adapter._app_id,
            "app_secret": adapter._app_secret,
            "log_level": "INFO",
            "event_handler": "handler",
            "domain": "https://open.feishu.cn",
        }])

    @patch.dict(os.environ, {}, clear=True)
    def test_edit_message_updates_existing_feishu_message(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        captured = {}

        class _MessageAPI:
            def update(self, request):
                captured["request"] = request
                return SimpleNamespace(success=lambda: True)

        adapter._client = SimpleNamespace(
            im=SimpleNamespace(
                v1=SimpleNamespace(
                    message=_MessageAPI(),
                )
            )
        )

        async def _direct(func, *args, **kwargs):
            return func(*args, **kwargs)

        with patch("plugins.platforms.feishu.adapter.asyncio.to_thread", side_effect=_direct):
            result = asyncio.run(
                adapter.edit_message(
                    chat_id="oc_chat",
                    message_id="om_progress",
                    content="📖 read_file: \"/tmp/image.png\"",
                )
            )

        self.assertTrue(result.success)
        self.assertEqual(result.message_id, "om_progress")
        self.assertEqual(captured["request"].message_id, "om_progress")
        self.assertEqual(captured["request"].request_body.msg_type, "text")
        self.assertEqual(
            captured["request"].request_body.content,
            json.dumps({"text": "📖 read_file: \"/tmp/image.png\""}, ensure_ascii=False),
        )


@patch.dict(os.environ, {}, clear=True)
class TestDeleteMessage(unittest.TestCase):
    def test_delete_message_calls_im_message_delete(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        captured = {"ids": []}

        class _MessageAPI:
            def delete(self, request):
                captured["ids"].append(getattr(request, "message_id", None))
                return SimpleNamespace(success=lambda: True)

        adapter = FeishuAdapter(PlatformConfig())
        adapter._client = SimpleNamespace(
            im=SimpleNamespace(v1=SimpleNamespace(message=_MessageAPI()))
        )

        async def _direct(func, *args, **kwargs):
            return func(*args, **kwargs)

        with patch.object(adapter, "_run_blocking", side_effect=_direct):
            ok = asyncio.run(adapter.delete_message("oc_chat", "om_preview"))

        self.assertTrue(ok)
        self.assertEqual(captured["ids"], ["om_preview"])


class TestAdapterModule(unittest.TestCase):
    def test_load_settings_uses_sdk_defaults_for_invalid_ws_reconnect_values(self):
        from plugins.platforms.feishu.adapter import FeishuAdapter

        settings = FeishuAdapter._load_settings(
            {
                "ws_reconnect_nonce": -1,
                "ws_reconnect_interval": "bad",
            }
        )

        self.assertEqual(settings.ws_reconnect_nonce, 30)
        self.assertEqual(settings.ws_reconnect_interval, 120)

    def test_runtime_ws_overrides_reapply_after_sdk_configure(self):
        import sys
        from types import ModuleType

        class _FakeWSClient:
            def __init__(self):
                self._reconnect_nonce = 30
                self._reconnect_interval = 120
                self._ping_interval = 120
                self.configure_calls = []

            def _configure(self, conf):
                self.configure_calls.append(conf)
                self._reconnect_nonce = conf.ReconnectNonce
                self._reconnect_interval = conf.ReconnectInterval
                self._ping_interval = conf.PingInterval

            def start(self):
                conf = SimpleNamespace(ReconnectNonce=99, ReconnectInterval=88, PingInterval=77)
                self._configure(conf)
                raise RuntimeError("stop test client")

        fake_client = _FakeWSClient()
        fake_adapter = SimpleNamespace(
            _loop=None,
            _ws_thread_loop=None,
            _ws_reconnect_nonce=2,
            _ws_reconnect_interval=3,
            _ws_ping_interval=4,
            _ws_ping_timeout=5,
        )
        fake_client_module = ModuleType("lark_oapi.ws.client")
        fake_client_module.loop = None
        fake_client_module.websockets = SimpleNamespace(connect=AsyncMock())
        fake_client_module.Client = type("Client", (), {"_receive_message_loop": lambda self: None})
        fake_ws_module = ModuleType("lark_oapi.ws")
        fake_ws_module.client = fake_client_module
        fake_root_module = ModuleType("lark_oapi")
        fake_root_module.ws = fake_ws_module

        original_modules = sys.modules.copy()
        sys.modules["lark_oapi"] = fake_root_module
        sys.modules["lark_oapi.ws"] = fake_ws_module
        sys.modules["lark_oapi.ws.client"] = fake_client_module
        try:
            from plugins.platforms.feishu.adapter import _run_official_feishu_ws_client

            _run_official_feishu_ws_client(fake_client, fake_adapter)
        finally:
            sys.modules.clear()
            sys.modules.update(original_modules)

        self.assertEqual(len(fake_client.configure_calls), 1)
        self.assertEqual(fake_client._reconnect_nonce, 2)
        self.assertEqual(fake_client._reconnect_interval, 3)
        self.assertEqual(fake_client._ping_interval, 4)

def _admits_group(adapter, message, sender_id, chat_id=""):
    """Group-path shim: run a message through ``_admit`` and return a bool."""
    sender = SimpleNamespace(sender_type="user", sender_id=sender_id)
    if not hasattr(message, "chat_type"):
        message.chat_type = "group"
    if chat_id:
        message.chat_id = chat_id
    return adapter._admit(sender, message) is None

class TestAdapterBehavior(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_build_event_handler_registers_reaction_and_card_processors(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        calls = []

        class _Builder:
            def register_p2_im_message_message_read_v1(self, _handler):
                calls.append("message_read")
                return self

            def register_p2_im_message_receive_v1(self, _handler):
                calls.append("message_receive")
                return self

            def register_p2_im_message_reaction_created_v1(self, _handler):
                calls.append("reaction_created")
                return self

            def register_p2_im_message_reaction_deleted_v1(self, _handler):
                calls.append("reaction_deleted")
                return self

            def register_p2_card_action_trigger(self, _handler):
                calls.append("card_action")
                return self

            def register_p2_im_chat_member_bot_added_v1(self, _handler):
                calls.append("bot_added")
                return self

            def register_p2_im_chat_member_bot_deleted_v1(self, _handler):
                calls.append("bot_deleted")
                return self

            def register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(self, _handler):
                calls.append("p2p_chat_entered")
                return self

            def register_p2_im_message_recalled_v1(self, _handler):
                calls.append("message_recalled")
                return self

            def register_p2_customized_event(self, event_key, _handler):
                calls.append(f"customized:{event_key}")
                return self

            def build(self):
                calls.append("build")
                return "handler"

        class _Dispatcher:
            @staticmethod
            def builder(_encrypt_key, _verification_token):
                calls.append("builder")
                return _Builder()

        with patch("plugins.platforms.feishu.adapter.EventDispatcherHandler", _Dispatcher):
            handler = adapter._build_event_handler()

        self.assertEqual(handler, "handler")
        # Contract: the processors this adapter depends on are registered (not their order/count).
        self.assertTrue(
            {"message_receive", "reaction_created", "reaction_deleted", "card_action"} <= set(calls),
            calls,
        )

    @patch.dict(os.environ, {}, clear=True)
    def test_bot_origin_reactions_are_dropped_to_avoid_feedback_loops(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        adapter._loop = object()

        for emoji in ("Typing", "CrossMark"):
            event = SimpleNamespace(
                message_id="om_msg",
                operator_type="bot",
                reaction_type=SimpleNamespace(emoji_type=emoji),
            )
            data = SimpleNamespace(event=event)
            with patch(
                "plugins.platforms.feishu.adapter.asyncio.run_coroutine_threadsafe"
            ) as run_threadsafe:
                adapter._on_reaction_event("im.message.reaction.created_v1", data)
            run_threadsafe.assert_not_called()

    @patch.dict(os.environ, {}, clear=True)
    def test_user_reaction_with_managed_emoji_is_still_routed(self):
        # Operator-origin filter is enough to prevent feedback loops; we must
        # not additionally swallow user-origin reactions just because their
        # emoji happens to collide with a lifecycle emoji.
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        adapter._loop = SimpleNamespace(is_closed=lambda: False)

        event = SimpleNamespace(
            message_id="om_msg",
            operator_type="user",
            reaction_type=SimpleNamespace(emoji_type="Typing"),
        )
        data = SimpleNamespace(event=event)

        def _close_coro_and_return_future(coro, _loop):
            coro.close()
            return SimpleNamespace(add_done_callback=lambda _: None)

        with patch(
            "plugins.platforms.feishu.adapter.asyncio.run_coroutine_threadsafe",
            side_effect=_close_coro_and_return_future,
        ) as run_threadsafe:
            adapter._on_reaction_event("im.message.reaction.created_v1", data)
        run_threadsafe.assert_called_once()

    def _build_reaction_adapter(self, *, msg_sender_id: str):
        """Build a FeishuAdapter wired up to return a single GET-message result."""
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        adapter._app_id = "cli_self_app"
        adapter._bot_open_id = "ou_self_bot"
        adapter._bot_user_id = "u_self_bot"

        msg = SimpleNamespace(
            sender=SimpleNamespace(sender_type="app", id=msg_sender_id, id_type="app_id"),
            chat_id="oc_chat",
            chat_type="group",
        )
        response = SimpleNamespace(success=lambda: True, data=SimpleNamespace(items=[msg]))
        adapter._client = SimpleNamespace(
            im=SimpleNamespace(
                v1=SimpleNamespace(message=SimpleNamespace(get=Mock(return_value=response)))
            )
        )
        adapter._build_get_message_request = Mock(return_value=object())
        adapter._handle_message_with_guards = AsyncMock()
        adapter._resolve_sender_profile = AsyncMock(
            return_value={"user_id": "u_human", "user_name": "Human", "user_id_alt": None}
        )
        adapter.get_chat_info = AsyncMock(return_value={"name": "Test Chat"})
        return adapter

    @patch.dict(os.environ, {}, clear=True)
    def test_reaction_on_peer_bot_message_is_not_routed(self):
        # GET im/v1/messages sender for bot messages carries id=app_id; a peer
        # bot's message has a different app_id than ours, so it must be dropped.
        adapter = self._build_reaction_adapter(msg_sender_id="cli_peer_app")

        event = SimpleNamespace(
            message_id="om_peer_msg",
            user_id=SimpleNamespace(open_id="ou_human", user_id=None, union_id=None),
            reaction_type=SimpleNamespace(emoji_type="THUMBSUP"),
        )
        data = SimpleNamespace(event=event)
        asyncio.run(
            adapter._handle_reaction_event("im.message.reaction.created_v1", data)
        )
        adapter._handle_message_with_guards.assert_not_awaited()

    def test_per_group_allowlist_policy_gates_by_sender(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        config = PlatformConfig(
            extra={
                "group_rules": {
                    "oc_chat_a": {
                        "policy": "allowlist",
                        "allowlist": ["ou_alice", "ou_bob"],
                    }
                }
            }
        )
        adapter = FeishuAdapter(config)
        adapter._bot_open_id = "ou_bot"

        message = SimpleNamespace(
            mentions=[SimpleNamespace(name="Bot", id=SimpleNamespace(open_id="ou_bot", user_id=None))]
        )

        self.assertTrue(
            _admits_group(adapter,
                message,
                SimpleNamespace(open_id="ou_alice", user_id=None),
                "oc_chat_a",
            )
        )
        self.assertFalse(
            _admits_group(adapter,
                message,
                SimpleNamespace(open_id="ou_charlie", user_id=None),
                "oc_chat_a",
            )
        )

    def test_global_admins_bypass_all_group_rules(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        config = PlatformConfig(
            extra={
                "admins": ["ou_admin"],
                "group_rules": {
                    "oc_chat_e": {
                        "policy": "allowlist",
                        "allowlist": ["ou_alice"],
                    }
                },
            }
        )
        adapter = FeishuAdapter(config)
        adapter._bot_open_id = "ou_bot"

        message = SimpleNamespace(
            mentions=[SimpleNamespace(name="Bot", id=SimpleNamespace(open_id="ou_bot", user_id=None))]
        )

        self.assertTrue(
            _admits_group(adapter,
                message,
                SimpleNamespace(open_id="ou_admin", user_id=None),
                "oc_chat_e",
            )
        )

    def test_default_group_policy_fallback_for_chats_without_explicit_rule(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        config = PlatformConfig(
            extra={
                "default_group_policy": "open",
            }
        )
        adapter = FeishuAdapter(config)
        adapter._bot_open_id = "ou_bot"

        message = SimpleNamespace(
            mentions=[SimpleNamespace(name="Bot", id=SimpleNamespace(open_id="ou_bot", user_id=None))]
        )

        self.assertTrue(
            _admits_group(adapter,
                message,
                SimpleNamespace(open_id="ou_anyone", user_id=None),
                "oc_chat_unknown",
            )
        )

    @patch.dict(os.environ, {"FEISHU_GROUP_POLICY": "open"}, clear=True)
    def test_group_message_matches_bot_name_when_only_name_available(self):
        """Name fallback engages when either side lacks an open_id. When BOTH
        the mention and the bot carry open_ids, IDs are authoritative — a
        same-name human with a different open_id must NOT admit."""
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        # Case 1: bot has only a name (open_id not hydrated / not configured).
        # Name fallback is the only available signal for any mention.
        adapter = FeishuAdapter(PlatformConfig())
        adapter._bot_name = "Hermes Bot"
        sender_id = SimpleNamespace(open_id="ou_any", user_id=None)

        name_only_mention = SimpleNamespace(
            name="Hermes Bot",
            id=SimpleNamespace(open_id=None, user_id=None),
        )
        different_mention = SimpleNamespace(
            name="Another Bot",
            id=SimpleNamespace(open_id=None, user_id=None),
        )

        self.assertTrue(
            _admits_group(adapter, SimpleNamespace(mentions=[name_only_mention]), sender_id, "")
        )
        self.assertFalse(
            _admits_group(adapter, SimpleNamespace(mentions=[different_mention]), sender_id, "")
        )

        # Case 2: bot's open_id IS known — a same-name human with different
        # open_id must NOT admit (IDs override names).
        adapter2 = FeishuAdapter(PlatformConfig())
        adapter2._bot_open_id = "ou_bot"
        adapter2._bot_name = "Hermes Bot"

        same_name_other_id_mention = SimpleNamespace(
            name="Hermes Bot",
            id=SimpleNamespace(open_id="ou_other", user_id="u_other"),
        )
        bot_mention = SimpleNamespace(
            name="Hermes Bot",
            id=SimpleNamespace(open_id="ou_bot", user_id=None),
        )

        self.assertFalse(
            _admits_group(
                adapter2,
                SimpleNamespace(mentions=[same_name_other_id_mention]),
                sender_id,
                "",
            )
        )
        self.assertTrue(
            _admits_group(adapter2, SimpleNamespace(mentions=[bot_mention]), sender_id, "")
        )

    @patch.dict(os.environ, {}, clear=True)
    def test_extract_post_message_downloads_embedded_resources(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        adapter._download_feishu_image = AsyncMock(return_value=("/tmp/feishu-image.png", "image/png"))
        adapter._download_feishu_message_resource = AsyncMock(return_value=("/tmp/spec.pdf", "application/pdf"))
        message = SimpleNamespace(
            message_type="post",
            content=(
                '{"en_us":{"title":"Rich message","content":['
                '[{"tag":"img","image_key":"img_123","alt":"diagram"}],'
                '[{"tag":"media","file_key":"file_123","file_name":"spec.pdf"}]'
                ']}}'
            ),
            message_id="om_post_media",
        )

        text, msg_type, media_urls, media_types, media_text_inlined, _mentions = asyncio.run(
            adapter._extract_message_content(message)
        )

        self.assertEqual(text, "Rich message\n[Image: diagram]\n[Attachment: spec.pdf]")
        self.assertEqual(msg_type.value, "text")
        self.assertEqual(media_urls, ["/tmp/feishu-image.png", "/tmp/spec.pdf"])
        self.assertEqual(media_types, ["image/png", "application/pdf"])
        self.assertEqual(media_text_inlined, [False, False])
        adapter._download_feishu_image.assert_awaited_once_with(
            message_id="om_post_media",
            image_key="img_123",
        )
        adapter._download_feishu_message_resource.assert_awaited_once_with(
            message_id="om_post_media",
            file_key="file_123",
            resource_type="file",
            fallback_filename="spec.pdf",
        )

    @patch.dict(os.environ, {}, clear=True)
    def test_extract_audio_message_downloads_and_caches(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        adapter._download_feishu_message_resource = AsyncMock(
            return_value=("/tmp/feishu-audio.ogg", "audio/ogg")
        )
        message = SimpleNamespace(
            message_type="audio",
            content='{"file_key":"file_audio","file_name":"voice.ogg"}',
            message_id="om_audio",
        )

        text, msg_type, media_urls, media_types, media_text_inlined, _mentions = asyncio.run(
            adapter._extract_message_content(message)
        )

        self.assertEqual(text, "")
        # Lark "audio" msg_type is a native voice recording (the fixture is
        # literally voice.ogg) — it must classify as VOICE so the gateway
        # auto-transcribes it, not AUDIO (a non-transcribed file attachment).
        # See the #28993 follow-up fix in _resolve_normalized_message_type.
        self.assertEqual(msg_type.value, "voice")
        self.assertEqual(media_urls, ["/tmp/feishu-audio.ogg"])
        self.assertEqual(media_types, ["audio/ogg"])
        self.assertEqual(media_text_inlined, [False])

    @patch.dict(os.environ, {}, clear=True)
    def test_extract_text_message_starting_with_slash_becomes_command(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        adapter._dispatch_inbound_event = AsyncMock()
        adapter.get_chat_info = AsyncMock(
            return_value={"chat_id": "oc_chat", "name": "Feishu DM", "type": "dm"}
        )
        adapter._resolve_sender_profile = AsyncMock(
            return_value={"user_id": "ou_user", "user_name": "张三", "user_id_alt": None}
        )
        message = SimpleNamespace(
            chat_id="oc_chat",
            thread_id=None,
            parent_id=None,
            upper_message_id=None,
            message_type="text",
            content='{"text":"/help test"}',
            message_id="om_command",
        )

        asyncio.run(
            adapter._process_inbound_message(
                data=SimpleNamespace(event=SimpleNamespace(message=message)),
                message=message,
                sender_id=SimpleNamespace(open_id="ou_user", user_id=None, union_id=None),
                is_bot=False,
                chat_type="p2p",
                message_id="om_command",
            )
        )

        event = adapter._dispatch_inbound_event.await_args.args[0]
        self.assertEqual(event.message_type.value, "command")
        self.assertEqual(event.text, "/help test")

    @patch.dict(os.environ, {}, clear=True)
    def test_extract_text_file_injects_content(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tmp:
            tmp.write("hello from feishu")
            path = tmp.name

        try:
            text = asyncio.run(adapter._maybe_extract_text_document(path, "text/plain"))
        finally:
            os.unlink(path)

        self.assertIn("hello from feishu", text)
        self.assertIn("[Content of", text)

    @patch.dict(os.environ, {}, clear=True)
    def test_message_event_submits_to_adapter_loop(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())

        class _Loop:
            def is_closed(self):
                return False

        adapter._loop = _Loop()

        message = SimpleNamespace(
            message_id="om_text",
            chat_type="p2p",
            chat_id="oc_chat",
            message_type="text",
            content='{"text":"hello"}',
        )
        sender_id = SimpleNamespace(open_id="ou_user", user_id=None, union_id=None)
        sender = SimpleNamespace(sender_id=sender_id, sender_type="user")
        data = SimpleNamespace(event=SimpleNamespace(message=message, sender=sender))

        future = SimpleNamespace(add_done_callback=lambda *_args, **_kwargs: None)

        def _submit(coro, _loop):
            coro.close()
            return future

        with patch("plugins.platforms.feishu.adapter.asyncio.run_coroutine_threadsafe", side_effect=_submit) as submit:
            adapter._on_message_event(data)

        self.assertTrue(submit.called)

    @patch.dict(os.environ, {}, clear=True)
    def test_webhook_request_uses_same_message_dispatch_path(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        adapter._on_message_event = Mock()

        body = json.dumps({
            "header": {"event_type": "im.message.receive_v1"},
            "event": {"message": {"message_id": "om_test"}},
        }).encode("utf-8")
        request = SimpleNamespace(
            remote="127.0.0.1",
            content_length=None,
            headers={},
            content=_FakeRequestContent(body),
        )

        response = asyncio.run(adapter._handle_webhook_request(request))

        self.assertEqual(response.status, 200)
        adapter._on_message_event.assert_called_once()

    @patch.dict(os.environ, {"FEISHU_VERIFICATION_TOKEN": "expected-token"}, clear=True)
    def test_url_verification_requires_configured_verification_token(self):
        """url_verification must be rejected when token is set but mismatched.

        Regression: previously the challenge was reflected before the token
        check, so an unauthenticated remote could prove endpoint control by
        sending an attacker-controlled challenge string.
        """
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        body = json.dumps({
            "type": "url_verification",
            "token": "wrong-token",
            "challenge": "attacker-controlled-challenge",
        }).encode("utf-8")
        request = SimpleNamespace(
            remote="203.0.113.10",
            content_length=None,
            headers={},
            content=_FakeRequestContent(body),
        )

        response = asyncio.run(adapter._handle_webhook_request(request))

        self.assertEqual(response.status, 401)

    @patch.dict(os.environ, {}, clear=True)
    def test_process_inbound_message_uses_event_sender_identity_only(self):
        from gateway.config import PlatformConfig
        from gateway.platforms.event import MessageType
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        adapter._dispatch_inbound_event = AsyncMock()
        # Sender name now comes from the contact API; mock it to return a known value.
        adapter._resolve_sender_name_from_api = AsyncMock(return_value="张三")
        adapter.get_chat_info = AsyncMock(
            return_value={"chat_id": "oc_chat", "name": "Feishu DM", "type": "dm"}
        )
        message = SimpleNamespace(
            chat_id="oc_chat",
            thread_id=None,
            message_type="text",
            content='{"text":"hello"}',
            message_id="om_text",
        )
        sender_id = SimpleNamespace(
            open_id="ou_user",
            user_id="u_user",
            union_id="on_union",
        )
        sender = SimpleNamespace(sender_type="user", sender_id=sender_id)
        data = SimpleNamespace(event=SimpleNamespace(message=message, sender=sender))

        asyncio.run(
            adapter._process_inbound_message(
                data=data,
                message=message,
                sender_id=sender.sender_id,
                chat_type="p2p",
                message_id="om_text",
            )
        )

        adapter._dispatch_inbound_event.assert_awaited_once()
        event = adapter._dispatch_inbound_event.await_args.args[0]
        self.assertEqual(event.message_type, MessageType.TEXT)
        self.assertEqual(event.source.user_id, "u_user")  # tenant-scoped user_id preferred over app-scoped open_id
        self.assertEqual(event.source.user_name, "张三")
        self.assertEqual(event.source.user_id_alt, "on_union")
        self.assertEqual(event.source.chat_name, "Feishu DM")
        # Reply anchors / slash-command thread checks read source.message_id, not event.message_id.
        self.assertEqual(event.source.message_id, "om_text")

    @patch.dict(
        os.environ,
        {
            "HERMES_FEISHU_TEXT_BATCH_MAX_MESSAGES": "2",
        },
        clear=True,
    )
    def test_text_batch_flushes_when_message_count_limit_is_hit(self):
        from gateway.config import PlatformConfig
        from gateway.platforms.event import MessageEvent, MessageType
        from plugins.platforms.feishu.adapter import FeishuAdapter
        from gateway.session import SessionSource

        adapter = FeishuAdapter(PlatformConfig())
        adapter.handle_message = AsyncMock()

        def _source(message_id: str) -> SessionSource:
            # Each inbound message carries its own source, pinned to that message's id.
            return SessionSource(
                platform=adapter.platform,
                chat_id="oc_chat",
                chat_name="Feishu DM",
                chat_type="dm",
                user_id="ou_user",
                user_name="张三",
                message_id=message_id,
            )

        async def _sleep(_delay):
            return None

        async def _run() -> None:
            with patch("plugins.platforms.feishu.adapter.asyncio.sleep", side_effect=_sleep):
                await adapter._dispatch_inbound_event(
                    MessageEvent(text="A", message_type=MessageType.TEXT, source=_source("om_1"), message_id="om_1")
                )
                await adapter._dispatch_inbound_event(
                    MessageEvent(text="B", message_type=MessageType.TEXT, source=_source("om_2"), message_id="om_2")
                )
                await adapter._dispatch_inbound_event(
                    MessageEvent(text="C", message_type=MessageType.TEXT, source=_source("om_3"), message_id="om_3")
                )
                pending = list(adapter._pending_text_batch_tasks.values())
                self.assertEqual(len(pending), 1)
                await asyncio.gather(*pending, return_exceptions=True)

        asyncio.run(_run())

        self.assertEqual(adapter.handle_message.await_count, 2)
        first = adapter.handle_message.await_args_list[0].args[0]
        second = adapter.handle_message.await_args_list[1].args[0]
        self.assertEqual(first.text, "A\nB")
        self.assertEqual(second.text, "C")
        # Coalescing advances the event id to the latest message; the reply anchor
        # (source.message_id) must move with it or replies/session tools disagree.
        self.assertEqual(first.message_id, "om_2")
        self.assertEqual(first.source.message_id, first.message_id)

    @patch.dict(
        os.environ,
        {
            "HERMES_FEISHU_TEXT_BATCH_MAX_MESSAGES": "8",
            "HERMES_FEISHU_TEXT_BATCH_MAX_CHARS": "4000",
        },
        clear=False,
    )
    def test_text_batch_preserves_later_message_attachments(self):
        from gateway.config import PlatformConfig
        from gateway.platforms.event import MessageEvent, MessageType
        from gateway.session import SessionSource
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        adapter.handle_message = AsyncMock()
        source = SessionSource(
            platform=adapter.platform,
            chat_id="oc_chat",
            chat_name="Feishu DM",
            chat_type="dm",
            user_id="ou_user",
            user_name="张三",
        )

        async def _run() -> None:
            await adapter._enqueue_text_event(
                MessageEvent(text="first", message_type=MessageType.TEXT, source=source)
            )
            await adapter._enqueue_text_event(
                MessageEvent(
                    text="second",
                    message_type=MessageType.TEXT,
                    source=source,
                    media_urls=["/cache/second.md"],
                    media_types=["text/markdown"],
                    media_text_inlined=[True],
                )
            )
            await adapter._flush_text_batch_now(adapter._text_batch_key(source_event))

        source_event = MessageEvent(text="", message_type=MessageType.TEXT, source=source)
        asyncio.run(_run())

        adapter.handle_message.assert_awaited_once()
        event = adapter.handle_message.await_args.args[0]
        self.assertEqual(event.text, "first\nsecond")
        self.assertEqual(event.media_urls, ["/cache/second.md"])
        self.assertEqual(event.media_types, ["text/markdown"])
        self.assertEqual(event.media_text_inlined, [True])

    @patch.dict(os.environ, {}, clear=True)
    def test_media_batch_merges_rapid_photo_messages(self):
        from gateway.config import PlatformConfig
        from gateway.platforms.event import MessageEvent, MessageType
        from plugins.platforms.feishu.adapter import FeishuAdapter
        from gateway.session import SessionSource

        adapter = FeishuAdapter(PlatformConfig())
        adapter.handle_message = AsyncMock()
        source = SessionSource(
            platform=adapter.platform,
            chat_id="oc_chat",
            chat_name="Feishu DM",
            chat_type="dm",
            user_id="ou_user",
            user_name="张三",
        )

        async def _sleep(_delay):
            return None

        async def _run() -> None:
            with patch("plugins.platforms.feishu.adapter.asyncio.sleep", side_effect=_sleep):
                await adapter._dispatch_inbound_event(
                    MessageEvent(
                        text="第一张",
                        message_type=MessageType.PHOTO,
                        source=source,
                        message_id="om_p1",
                        media_urls=["/tmp/a.png"],
                        media_types=["image/png"],
                        media_text_inlined=[False],
                    )
                )
                await adapter._dispatch_inbound_event(
                    MessageEvent(
                        text="第二张",
                        message_type=MessageType.PHOTO,
                        source=source,
                        message_id="om_p2",
                        media_urls=["/tmp/b.png"],
                        media_types=["image/png"],
                        media_text_inlined=[True],
                    )
                )
                pending = list(adapter._pending_media_batch_tasks.values())
                self.assertEqual(len(pending), 1)
                await asyncio.gather(*pending, return_exceptions=True)

        asyncio.run(_run())

        adapter.handle_message.assert_awaited_once()
        event = adapter.handle_message.await_args.args[0]
        self.assertEqual(event.media_urls, ["/tmp/a.png", "/tmp/b.png"])
        self.assertEqual(event.media_text_inlined, [False, True])
        self.assertIn("第一张", event.text)
        self.assertIn("第二张", event.text)

    def test_download_remote_document_reads_response_before_httpx_client_closes(self):
        """#18451 — snapshot Content-Type + body while the httpx.AsyncClient
        context is still active so pooled connections fully release on
        exit; the body is streamed, so a read after close would fail."""
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        events: list[str] = []

        class _FakeResponse:
            headers = {"Content-Type": "application/octet-stream"}

            async def __aenter__(self) -> "_FakeResponse":
                return self
            async def __aexit__(self, *exc: object) -> None:
                events.append("response_exit")

            def raise_for_status(self) -> None:
                events.append("raise_for_status")

            async def aiter_bytes(self):
                events.append("content_read")
                yield b"doc-bytes"

        class _FakeAsyncClient:
            def __init__(self, *_a: object, **_k: object) -> None:
                pass

            async def __aenter__(self) -> "_FakeAsyncClient":
                events.append("client_enter")
                return self

            async def __aexit__(self, *exc: object) -> None:
                events.append("client_exit")

            def stream(self, *_a: object, **_k: object) -> _FakeResponse:
                events.append("get")
                return _FakeResponse()

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"HERMES_HOME": tmp}, clear=False):
                adapter = FeishuAdapter(PlatformConfig())

                async def _run() -> tuple[str, str]:
                    with patch("tools.url_safety.is_safe_url", return_value=True):
                        with patch(
                            "tools.url_safety.create_ssrf_safe_async_client",
                            side_effect=lambda **_kwargs: _FakeAsyncClient(),
                        ):
                            with patch(
                                "plugins.platforms.feishu.adapter.cache_document_from_bytes_async",
                                new=AsyncMock(return_value="/tmp/cached-doc.bin"),
                            ):
                                return await adapter._download_remote_document(
                                    "https://example.com/doc.bin",
                                    default_ext=".bin",
                                    preferred_name="doc",
                                )

                path, filename = asyncio.run(_run())

        self.assertEqual(path, "/tmp/cached-doc.bin")
        self.assertTrue(filename)
        # content_read MUST happen before client_exit (no read after the pool is torn down).
        self.assertLess(events.index("content_read"), events.index("client_exit"))

    def test_download_remote_document_blocks_connect_time_rebind(self):
        import httpcore
        from httpcore._backends.auto import AutoBackend
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter
        from tools.url_safety import SSRFConnectionBlocked

        adapter = FeishuAdapter(PlatformConfig())
        answers = iter(("93.184.216.34", "169.254.169.254"))

        def fake_getaddrinfo(_host, port, *_args, **_kwargs):
            ip = next(answers)
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))
            ]

        connect_attempts = []

        async def fake_connect_tcp(
            _self,
            host,
            port,
            timeout=None,
            local_address=None,
            socket_options=None,
        ):
            connect_attempts.append((host, port))
            raise httpcore.ConnectError("stop before network")

        proxy_vars = {
            name: ""
            for name in (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy",
            )
        }
        with (
            patch.dict(os.environ, proxy_vars, clear=False),
            patch("socket.getaddrinfo", side_effect=fake_getaddrinfo),
            patch.object(AutoBackend, "connect_tcp", new=fake_connect_tcp),
            self.assertRaises(SSRFConnectionBlocked),
        ):
            asyncio.run(
                adapter._download_remote_document(
                    "http://rebind.example/doc.bin",
                    default_ext=".bin",
                    preferred_name="doc",
                )
            )

        self.assertEqual(connect_attempts, [])

    def test_dedup_state_persists_across_adapter_restart(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        with tempfile.TemporaryDirectory() as temp_home:
            with patch.dict(os.environ, {"HERMES_HOME": temp_home}, clear=False):
                first = FeishuAdapter(PlatformConfig())
                self.assertFalse(asyncio.run(first._is_duplicate("om_same")))
                second = FeishuAdapter(PlatformConfig())
                self.assertTrue(asyncio.run(second._is_duplicate("om_same")))

    @patch.dict(os.environ, {}, clear=True)
    def test_send_document_reply_uses_thread_flag(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        captured = {}

        class _FileAPI:
            def create(self, request):
                return SimpleNamespace(
                    success=lambda: True,
                    data=SimpleNamespace(file_key="file_123"),
                )

        class _MessageAPI:
            def reply(self, request):
                captured["request"] = request
                return SimpleNamespace(
                    success=lambda: True,
                    data=SimpleNamespace(message_id="om_file_reply"),
                )

        adapter._client = SimpleNamespace(
            im=SimpleNamespace(
                v1=SimpleNamespace(
                    file=_FileAPI(),
                    message=_MessageAPI(),
                )
            )
        )

        async def _direct(func, *args, **kwargs):
            return func(*args, **kwargs)

        with tempfile.NamedTemporaryFile("wb", suffix=".pdf", delete=False) as tmp:
            tmp.write(b"%PDF-1.4 test")
            file_path = tmp.name

        try:
            with patch("plugins.platforms.feishu.adapter.asyncio.to_thread", side_effect=_direct):
                result = asyncio.run(
                    adapter.send_document(
                        chat_id="oc_chat",
                        file_path=file_path,
                        reply_to="om_parent",
                        metadata={"thread_id": "omt-thread"},
                    )
                )
        finally:
            os.unlink(file_path)

        self.assertTrue(result.success)
        self.assertTrue(captured["request"].request_body.reply_in_thread)

    @patch.dict(os.environ, {}, clear=True)
    def test_send_uses_post_for_every_chunk_of_multi_chunk_markdown(self):
        """Regression for #26841: when a long Markdown message is split
        across multiple chunks, every chunk must go out as
        ``msg_type=post`` — including chunk 1.  The bug was that the
        first chunk often had only plain prose (the per-chunk regex
        didn't match) and was sent as ``text``, so users saw literal
        ``**bold``/``## heading``/code fences while later chunks
        rendered correctly.
        """
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        captured = []

        class _MessageAPI:
            def create(self, request):
                captured.append(request)
                return SimpleNamespace(
                    success=lambda: True,
                    data=SimpleNamespace(
                        message_id=f"om_chunk_{len(captured)}",
                    ),
                )

        adapter._client = SimpleNamespace(
            im=SimpleNamespace(
                v1=SimpleNamespace(
                    message=_MessageAPI(),
                )
            )
        )

        async def _direct(func, *args, **kwargs):
            return func(*args, **kwargs)

        # Force a deterministic split so the test doesn't depend on the
        # exact 8000-char limit.  Chunk 1 is plain prose; chunk 2 has
        # the markdown markers.  Without the fix, chunk 1 went out as
        # ``msg_type=text``.
        first_chunk = "Here is a short intro that has no markdown markers at all."
        second_chunk = "## Heading\nAnd then some **bold** text."

        with patch.object(
            adapter, "truncate_message", return_value=[first_chunk, second_chunk],
        ), patch("plugins.platforms.feishu.adapter.asyncio.to_thread", side_effect=_direct):
            result = asyncio.run(
                adapter.send(
                    chat_id="oc_chat",
                    content=first_chunk + "\n" + second_chunk,
                )
            )

        self.assertTrue(result.success)
        self.assertEqual(len(captured), 2)
        msg_types = [r.request_body.msg_type for r in captured]
        self.assertEqual(msg_types, ["post", "post"])

    @patch.dict(os.environ, {}, clear=True)
    def test_send_splits_fenced_code_blocks_into_separate_post_rows(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        captured = {}

        class _MessageAPI:
            def create(self, request):
                captured["request"] = request
                return SimpleNamespace(
                    success=lambda: True,
                    data=SimpleNamespace(message_id="om_codeblock"),
                )

        adapter._client = SimpleNamespace(
            im=SimpleNamespace(
                v1=SimpleNamespace(
                    message=_MessageAPI(),
                )
            )
        )

        async def _direct(func, *args, **kwargs):
            return func(*args, **kwargs)

        content = (
            "确认已入库 ✓\n"
            "文件路径：`/root/.hermes/profiles/agent_cto/cron/jobs.json`\n"
            "**解码后的内容：**\n"
            "```json\n"
            '{"cron": "list"}\n'
            "```\n"
            "后续说明仍应保留。"
        )

        with patch("plugins.platforms.feishu.adapter.asyncio.to_thread", side_effect=_direct):
            result = asyncio.run(
                adapter.send(
                    chat_id="oc_chat",
                    content=content,
                )
            )

        self.assertTrue(result.success)
        self.assertEqual(captured["request"].request_body.msg_type, "post")
        payload = json.loads(captured["request"].request_body.content)
        rows = payload["zh_cn"]["content"]
        self.assertEqual(
            rows,
            [
                [
                    {
                        "tag": "md",
                        "text": "确认已入库 ✓\n文件路径：`/root/.hermes/profiles/agent_cto/cron/jobs.json`\n**解码后的内容：**",
                    }
                ],
                [{"tag": "md", "text": "```json\n{\"cron\": \"list\"}\n```"}],
                [{"tag": "md", "text": "后续说明仍应保留。"}],
            ],
        )

    @patch.dict(os.environ, {}, clear=True)
    def test_media_handling_is_untouched_when_card_output_is_off(self):
        """Card mode extracts attachments out of card text and dedupes uploads.

        With ``FEISHU_CARD_OUTPUT`` off, neither may happen: the text goes out
        exactly as given (tag included — the gateway strips it upstream on this
        path) and the same file can be sent as many times as the caller asks.
        """
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        self.assertFalse(adapter._card_output_enabled)

        with tempfile.NamedTemporaryFile("wb", suffix=".pdf", delete=False) as tmp:
            tmp.write(b"%PDF-1.4 test")
            file_path = tmp.name

        try:
            text = f"see the report MEDIA:{file_path}"
            body, owed = adapter._split_card_mode_attachments(
                chat_id="oc_chat", content=text, metadata=None,
            )
            self.assertEqual(body, text)
            self.assertEqual(owed, [])

            # The per-turn ledger is inert too, so nothing is ever skipped.
            self.assertTrue(adapter._claim_card_media("oc_chat", file_path, None))
            self.assertTrue(adapter._claim_card_media("oc_chat", file_path, None))
        finally:
            os.unlink(file_path)

@unittest.skipUnless(_HAS_LARK_OAPI, "lark-oapi not installed")
class TestPendingInboundQueue(unittest.TestCase):
    """Tests for the loop-not-ready race (#5499): inbound events arriving
    before or during adapter loop transitions must be queued for replay
    rather than silently dropped."""

    @patch.dict(os.environ, {}, clear=True)
    def test_event_queued_when_loop_not_ready(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        adapter._loop = None  # Simulate "before start()" or "during reconnect"

        with patch("plugins.platforms.feishu.adapter.threading.Thread") as thread_cls:
            adapter._on_message_event(SimpleNamespace(tag="evt-1"))
            adapter._on_message_event(SimpleNamespace(tag="evt-2"))
            adapter._on_message_event(SimpleNamespace(tag="evt-3"))

        # All three queued, none dropped.
        self.assertEqual(len(adapter._pending_inbound_events), 3)
        # Only ONE drainer thread scheduled, not one per event.
        self.assertEqual(thread_cls.call_count, 1)
        # Drain scheduled flag set.
        self.assertTrue(adapter._pending_drain_scheduled)

    @patch.dict(os.environ, {}, clear=True)
    def test_drainer_replays_queued_events_when_loop_becomes_ready(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        adapter._loop = None
        adapter._running = True

        class _ReadyLoop:
            def is_closed(self):
                return False

        # Queue three events while loop is None (simulate the race).
        events = [SimpleNamespace(tag=f"evt-{i}") for i in range(3)]
        with patch("plugins.platforms.feishu.adapter.threading.Thread"):
            for ev in events:
                adapter._on_message_event(ev)

        self.assertEqual(len(adapter._pending_inbound_events), 3)

        # Now the loop becomes ready; run the drainer inline (not as a thread)
        # to verify it replays the queue.
        adapter._loop = _ReadyLoop()

        future = SimpleNamespace(add_done_callback=lambda *_a, **_kw: None)
        submitted: list = []

        def _submit(coro, _loop):
            submitted.append(coro)
            coro.close()
            return future

        with patch(
            "plugins.platforms.feishu.adapter.asyncio.run_coroutine_threadsafe",
            side_effect=_submit,
        ) as submit:
            adapter._drain_pending_inbound_events()

        # All three events dispatched to the loop.
        self.assertEqual(submit.call_count, 3)
        # Queue emptied.
        self.assertEqual(len(adapter._pending_inbound_events), 0)
        # Drain flag reset so a future race can schedule a new drainer.
        self.assertFalse(adapter._pending_drain_scheduled)

@unittest.skipUnless(_HAS_LARK_OAPI, "lark-oapi not installed")
class TestWebhookSecurity(unittest.TestCase):
    """Tests for webhook signature verification, rate limiting, and body size limits."""

    def _make_adapter(self, encrypt_key: str = "") -> "FeishuAdapter":
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        with patch.dict(os.environ, {"FEISHU_APP_ID": "cli", "FEISHU_APP_SECRET": "sec", "FEISHU_ENCRYPT_KEY": encrypt_key}, clear=True):
            return FeishuAdapter(PlatformConfig())

    def test_signature_valid_passes(self):
        import hashlib

        encrypt_key = "test_secret"
        adapter = self._make_adapter(encrypt_key)
        body = b'{"type":"event"}'
        timestamp = str(int(time.time()))
        nonce = "abc123"
        content = f"{timestamp}{nonce}{encrypt_key}" + body.decode("utf-8")
        sig = hashlib.sha256(content.encode("utf-8")).hexdigest()
        headers = {"x-lark-request-timestamp": timestamp, "x-lark-request-nonce": nonce, "x-lark-signature": sig}
        self.assertTrue(adapter._is_webhook_signature_valid(headers, body))

    def test_rate_limit_resets_after_window_expires(self):
        from plugins.platforms.feishu.adapter import _FEISHU_WEBHOOK_RATE_LIMIT_MAX, _FEISHU_WEBHOOK_RATE_WINDOW_SECONDS
        adapter = self._make_adapter()
        ip = "10.0.0.3"
        for _ in range(_FEISHU_WEBHOOK_RATE_LIMIT_MAX):
            adapter._check_webhook_rate_limit(ip)
        self.assertFalse(adapter._check_webhook_rate_limit(ip))
        # Simulate window expiry by backdating the stored entry.
        count, window_start = adapter._webhook_rate_counts[ip]
        adapter._webhook_rate_counts[ip] = (count, window_start - _FEISHU_WEBHOOK_RATE_WINDOW_SECONDS - 1)
        self.assertTrue(adapter._check_webhook_rate_limit(ip))

    def test_webhook_request_rejects_oversized_chunked_body_while_reading(self):
        from gateway.config import PlatformConfig
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override
        from plugins.platforms.feishu.adapter import FeishuAdapter, _FEISHU_WEBHOOK_MAX_BODY_BYTES

        with tempfile.TemporaryDirectory() as tmpdir:
            token = set_hermes_home_override(tmpdir)
            try:
                adapter = FeishuAdapter(PlatformConfig())
            finally:
                reset_hermes_home_override(token)
            content = _FakeRequestContent(b"A" * (_FEISHU_WEBHOOK_MAX_BODY_BYTES + 2))
            request = SimpleNamespace(
                remote="127.0.0.1",
                content_length=None,
                headers={},
                content=content,
            )

            response = asyncio.run(adapter._handle_webhook_request(request))

            self.assertEqual(response.status, 413)
            self.assertEqual(content.read_sizes, [_FEISHU_WEBHOOK_MAX_BODY_BYTES + 1])

    @patch.dict(os.environ, {}, clear=True)
    def test_webhook_connect_requires_inbound_auth_secret(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(
            PlatformConfig(
                enabled=True,
                extra={"app_id": "cli_app", "app_secret": "secret_app", "connection_mode": "webhook"},
            )
        )
        self.assertFalse(asyncio.run(adapter.connect()))

    @patch.dict(os.environ, {}, clear=True)
    def test_webhook_loads_auth_secrets_from_platform_extra(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(
            PlatformConfig(
                enabled=True,
                extra={
                    "app_id": "cli_app",
                    "app_secret": "secret_app",
                    "connection_mode": "webhook",
                    "verification_token": "token_from_extra",
                    "encrypt_key": "encrypt_from_extra",
                },
            )
        )
        self.assertEqual(adapter._verification_token, "token_from_extra")
        self.assertEqual(adapter._encrypt_key, "encrypt_from_extra")

class TestDedupTTL(unittest.TestCase):
    """Tests for TTL-aware deduplication."""

    @patch.dict(os.environ, {}, clear=True)
    def test_duplicate_within_ttl_is_rejected(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        with patch.object(adapter, "_persist_seen_message_ids"):
            adapter._seen_message_ids = {"om_dup": time.time()}
            adapter._seen_message_order = ["om_dup"]
            self.assertTrue(asyncio.run(adapter._is_duplicate("om_dup")))

    @patch.dict(os.environ, {}, clear=True)
    def test_load_tolerates_malformed_timestamp_values(self):
        """Regression #13632 — a non-numeric timestamp in the persisted
        dedup state must not crash adapter startup.  The bad key is
        skipped; the rest of the state loads.
        """
        import tempfile
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        with tempfile.TemporaryDirectory() as temp_home:
            with patch.dict(os.environ, {"HERMES_HOME": temp_home}, clear=True):
                adapter = FeishuAdapter(PlatformConfig())
                adapter._dedup_state_path.parent.mkdir(parents=True, exist_ok=True)
                adapter._dedup_state_path.write_text(
                    json.dumps(
                        {
                            "message_ids": {
                                "om_good": time.time(),
                                "om_bad_str": "not-a-timestamp",
                                "om_bad_null": None,
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                adapter._load_seen_message_ids()
                assert "om_good" in adapter._seen_message_ids
                assert "om_bad_str" not in adapter._seen_message_ids
                assert "om_bad_null" not in adapter._seen_message_ids

    @patch.dict(os.environ, {}, clear=True)
    def test_persist_on_new_message_runs_off_event_loop_thread(self):
        """atomic_json_write() calls os.fsync(), which blocks until the write
        reaches stable storage. _is_duplicate() runs on the event loop for
        every inbound message (_handle_message_event_data), so the persist
        step must be offloaded to a thread — mirrors
        test_directory_write_runs_off_event_loop_thread in
        test_channel_directory.py for the same #83906 bug class."""
        import threading
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        loop_thread = threading.get_ident()
        write_threads = []

        def fake_write(path, data, *args, **kwargs):
            write_threads.append(threading.get_ident())

        with patch("plugins.platforms.feishu.adapter.atomic_json_write", side_effect=fake_write):
            is_dup = asyncio.run(adapter._is_duplicate("om_new"))

        self.assertFalse(is_dup)
        self.assertTrue(write_threads)
        self.assertTrue(all(tid != loop_thread for tid in write_threads))

    @patch.dict(os.environ, {}, clear=True)
    def test_concurrent_dedup_persists_land_in_order(self):
        """Two in-flight _is_duplicate() calls (two chats) must not let an
        older seen-ids snapshot overwrite a newer one on disk."""
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        # The class-level env wipe drops the per-test HERMES_HOME, so the adapter resolves
        # its dedup store under the operator's real ~/.hermes and every id a live gateway
        # persisted leaks into writes[-1]. Pin the store to a scratch path instead.
        # Kept open for the whole test: _persist_seen_message_ids mkdirs the store's
        # parent back, so closing it early leaks an empty scratch dir per run.
        with tempfile.TemporaryDirectory() as scratch:
            with patch("plugins.platforms.feishu.adapter.get_hermes_home", return_value=Path(scratch)):
                adapter = FeishuAdapter(PlatformConfig())
            writes = []
            calls = [0]

            def slow_first_write(path, data, *args, **kwargs):
                idx = calls[0]
                calls[0] += 1
                if idx == 0:
                    time.sleep(0.05)
                writes.append(sorted(data["message_ids"]))

            async def run():
                first = asyncio.create_task(adapter._is_duplicate("om_a"))
                await asyncio.sleep(0.005)
                second = asyncio.create_task(adapter._is_duplicate("om_b"))
                await asyncio.gather(first, second)

            with patch("plugins.platforms.feishu.adapter.atomic_json_write", side_effect=slow_first_write):
                asyncio.run(run())

        self.assertEqual(writes[-1], ["om_a", "om_b"])

class TestGroupMentionAtAll(unittest.TestCase):
    """Tests for @_all (Feishu @everyone) group mention routing."""

    @patch.dict(os.environ, {"FEISHU_GROUP_POLICY": "allowlist", "FEISHU_ALLOWED_USERS": "ou_allowed"}, clear=True)
    def test_at_all_still_requires_policy_gate(self):
        """@_all bypasses mention gating but NOT the allowlist policy."""
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        message = SimpleNamespace(content='{"text":"@_all attention"}', mentions=[])
        # Non-allowlisted user — should be blocked even with @_all.
        blocked_sender = SimpleNamespace(open_id="ou_blocked", user_id=None)
        self.assertFalse(_admits_group(adapter, message, blocked_sender, ""))
        # Allowlisted user — should pass.
        allowed_sender = SimpleNamespace(open_id="ou_allowed", user_id=None)
        self.assertTrue(_admits_group(adapter, message, allowed_sender, ""))

    # ------------------------------------------------------------- env toggle

    # ------------------------------------------------------------- LRU bounds

class TestFeishuMentionMap(unittest.TestCase):

    def test_build_mentions_map_marks_self_by_open_id(self):
        from plugins.platforms.feishu.adapter import _build_mentions_map, _FeishuBotIdentity

        mention = SimpleNamespace(
            key="@_user_1",
            id=SimpleNamespace(open_id="ou_bot", user_id=""),
            name="Hermes",
        )
        ref = _build_mentions_map([mention], _FeishuBotIdentity(open_id="ou_bot"))["@_user_1"]
        self.assertTrue(ref.is_self)
        self.assertEqual(ref.open_id, "ou_bot")
        self.assertEqual(ref.name, "Hermes")

    def test_build_mentions_map_name_match_does_not_override_mismatching_open_id(self):
        """Regression: a human user whose display name matches the bot must
        NOT be flagged as self when their open_id differs. Before the fix,
        name-match fired even when open_id was present and different, causing
        their messages to be silently stripped/dropped."""
        from plugins.platforms.feishu.adapter import _build_mentions_map, _FeishuBotIdentity

        human_with_same_name = SimpleNamespace(
            key="@_user_1",
            id=SimpleNamespace(open_id="ou_human", user_id=""),
            name="Hermes Bot",
        )
        result = _build_mentions_map(
            [human_with_same_name],
            _FeishuBotIdentity(open_id="ou_bot", name="Hermes Bot"),
        )
        self.assertFalse(result["@_user_1"].is_self)

    def test_build_mentions_map_falls_back_to_name_when_bot_open_id_not_hydrated(self):
        """Regression: right after gateway startup, _hydrate_bot_identity may
        not have populated _bot_open_id yet. During that window, a mention
        carrying a real open_id should still match via name — otherwise
        @bot messages silently fail admission."""
        from plugins.platforms.feishu.adapter import _build_mentions_map, _FeishuBotIdentity

        bot_mention = SimpleNamespace(
            key="@_user_1",
            id=SimpleNamespace(open_id="ou_bot_actual", user_id=""),
            name="Hermes Bot",
        )
        # Bot identity has name but no open_id yet (hydration pending).
        result = _build_mentions_map(
            [bot_mention],
            _FeishuBotIdentity(open_id="", name="Hermes Bot"),
        )
        self.assertTrue(result["@_user_1"].is_self)

class TestFeishuMentionHint(unittest.TestCase):

    def test_hint_filters_self_mentions(self):
        from plugins.platforms.feishu.adapter import FeishuMentionRef, _build_mention_hint

        refs = [
            FeishuMentionRef(name="Hermes", open_id="ou_bot", is_self=True),
            FeishuMentionRef(name="Alice", open_id="ou_alice"),
        ]
        self.assertEqual(
            _build_mention_hint(refs),
            "[Mentioned: Alice (open_id=ou_alice)]",
        )

    def test_hint_dedupes_repeated_user(self):
        from plugins.platforms.feishu.adapter import FeishuMentionRef, _build_mention_hint

        refs = [
            FeishuMentionRef(name="Alice", open_id="ou_alice"),
            FeishuMentionRef(name="Alice", open_id="ou_alice"),
            FeishuMentionRef(name="Bob", open_id="ou_bob"),
        ]
        self.assertEqual(
            _build_mention_hint(refs),
            "[Mentioned: Alice (open_id=ou_alice), Bob (open_id=ou_bob)]",
        )

class TestFeishuStripLeadingSelf(unittest.TestCase):
    def _make_refs(self, *, self_name="Hermes", other_name=None):
        from plugins.platforms.feishu.adapter import FeishuMentionRef

        refs = [FeishuMentionRef(name=self_name, open_id="ou_bot", is_self=True)]
        if other_name:
            refs.append(FeishuMentionRef(name=other_name, open_id="ou_alice"))
        return refs

    def test_stops_at_first_non_self_token(self):
        from plugins.platforms.feishu.adapter import _strip_edge_self_mentions

        result = _strip_edge_self_mentions(
            "@Hermes @Alice make a group", self._make_refs(other_name="Alice")
        )
        self.assertEqual(result, "@Alice make a group")

    def test_strips_trailing_self_with_terminal_punct(self):
        from plugins.platforms.feishu.adapter import _strip_edge_self_mentions

        # Terminal punct after the mention — strip the mention, keep the punct.
        result = _strip_edge_self_mentions("look up docs @Hermes.", self._make_refs())
        self.assertEqual(result, "look up docs.")

    def test_preserves_trailing_self_before_non_terminal_char(self):
        from plugins.platforms.feishu.adapter import _strip_edge_self_mentions

        # Non-terminal char (here a Chinese particle) follows — preserve.
        result = _strip_edge_self_mentions(
            "please don't @Hermes anymore", self._make_refs()
        )
        self.assertEqual(result, "please don't @Hermes anymore")

    def test_returns_input_when_no_self_refs(self):
        from plugins.platforms.feishu.adapter import _strip_edge_self_mentions, FeishuMentionRef

        refs = [FeishuMentionRef(name="Alice", open_id="ou_alice")]
        self.assertEqual(_strip_edge_self_mentions("@Alice hi", refs), "@Alice hi")

class TestFeishuNormalizeText(unittest.TestCase):

    def test_renders_self_mention_with_name(self):
        from plugins.platforms.feishu.adapter import _normalize_feishu_text, FeishuMentionRef

        refs = {"@_user_1": FeishuMentionRef(name="Hermes", open_id="ou_bot", is_self=True)}
        self.assertEqual(
            _normalize_feishu_text("stop pinging @_user_1 please", refs),
            "stop pinging @Hermes please",
        )

    def test_at_all_rendered_as_english_literal(self):
        from plugins.platforms.feishu.adapter import _normalize_feishu_text

        self.assertEqual(_normalize_feishu_text("@_all notice", None), "@all notice")

class TestFeishuPostMentionParsing(unittest.TestCase):
    def test_post_at_tag_renders_via_mentions_map(self):
        """Post <at>.user_id is a placeholder ('@_user_N'); the real display
        name comes from the mentions_map lookup. Confirmed via live
        im.v1.message.get payload."""
        from plugins.platforms.feishu.adapter import parse_feishu_post_payload, FeishuMentionRef

        payload = {
            "en_us": {
                "content": [[
                    {"tag": "at", "user_id": "@_user_1", "user_name": "ignored"},
                    {"tag": "text", "text": " hello"},
                ]]
            }
        }
        mentions_map = {
            "@_user_1": FeishuMentionRef(name="Alice", open_id="ou_alice"),
        }
        result = parse_feishu_post_payload(payload, mentions_map=mentions_map)
        self.assertEqual(result.text_content, "@Alice hello")

class TestFeishuPostFileParsing(unittest.TestCase):
    def test_collects_valid_top_level_files_and_dedupes_inline_refs(self):
        from plugins.platforms.feishu.adapter import parse_feishu_post_payload

        result = parse_feishu_post_payload({
            "title": "",
            "content": [[
                {"tag": "text", "text": "Review these"},
                {"tag": "file", "file_key": "file_1", "file_name": "first.md"},
            ]],
            "files": [
                {"file_key": "file_1", "file_name": "first.md", "is_folder": False},
                {"file_key": "file_2", "file_name": "second.txt", "is_folder": False},
                {"file_key": "folder_1", "file_name": "folder", "is_folder": True},
                {"file_name": "missing-key.md", "is_folder": False},
            ],
        })

        self.assertEqual([ref.file_key for ref in result.media_refs], ["file_1", "file_2"])
        self.assertEqual(
            result.text_content,
            "Review these[Attachment: first.md]\n[Attachment: second.txt]",
        )

class TestFeishuPostTextIsNotMarkdownEscaped(unittest.TestCase):
    def test_text_elements_keep_markdown_characters_and_style_wrappers(self):
        """Inbound post text reaches the model verbatim (no backslash escapes) while the
        structured style flags still render as markdown (#9816)."""
        from plugins.platforms.feishu.adapter import parse_feishu_post_payload

        payload = {"zh_cn": {"content": [[
            {"tag": "text", "text": "run `print('hi')` for **emphasis** [x](y)"},
            {"tag": "text", "text": "strong", "style": {"bold": True}},
        ]]}}
        text = parse_feishu_post_payload(payload).text_content
        self.assertIn("run `print('hi')` for **emphasis** [x](y)", text)
        self.assertIn("**strong**", text)
        self.assertNotIn("\\", text)

class TestFeishuNormalizeWithMentions(unittest.TestCase):
    def test_text_message_renders_mention_by_name(self):
        from plugins.platforms.feishu.adapter import normalize_feishu_message, _FeishuBotIdentity

        mention = SimpleNamespace(
            key="@_user_1",
            id=SimpleNamespace(open_id="ou_alice", user_id=""),
            name="Alice",
        )
        normalized = normalize_feishu_message(
            message_type="text",
            raw_content=json.dumps({"text": "@_user_1 hello"}),
            mentions=[mention],
            bot=_FeishuBotIdentity(open_id="ou_bot"),
        )
        self.assertEqual(normalized.text_content, "@Alice hello")
        self.assertEqual(len(normalized.mentions), 1)
        self.assertEqual(normalized.mentions[0].open_id, "ou_alice")
        self.assertFalse(normalized.mentions[0].is_self)

    def test_post_message_marks_self_via_mentions_map_lookup(self):
        """Real Feishu post: <at user_id="@_user_N"> + top-level mentions array
        resolves to open_id via placeholder lookup, not direct tag fields."""
        from plugins.platforms.feishu.adapter import normalize_feishu_message, _FeishuBotIdentity

        raw = json.dumps({
            "en_us": {
                "content": [
                    [
                        {"tag": "at", "user_id": "@_user_1", "user_name": "Hermes"},
                        {"tag": "text", "text": " check this"},
                    ]
                ]
            }
        })
        bot_mention = SimpleNamespace(
            key="@_user_1",
            id=SimpleNamespace(open_id="ou_bot", user_id=""),
            name="Hermes",
        )
        normalized = normalize_feishu_message(
            message_type="post",
            raw_content=raw,
            mentions=[bot_mention],
            bot=_FeishuBotIdentity(open_id="ou_bot"),
        )
        self.assertEqual(len(normalized.mentions), 1)
        self.assertTrue(normalized.mentions[0].is_self)
        self.assertEqual(normalized.mentions[0].open_id, "ou_bot")

class TestFeishuPostMentionsBot(unittest.TestCase):
    def _build_adapter(self, bot_open_id="ou_bot", bot_user_id="", bot_name=""):
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter.__new__(FeishuAdapter)
        adapter._bot_open_id = bot_open_id
        adapter._bot_user_id = bot_user_id
        adapter._bot_name = bot_name
        return adapter

    def test_post_mentions_bot_uses_is_self_flag(self):
        from plugins.platforms.feishu.adapter import FeishuMentionRef

        adapter = self._build_adapter()
        self.assertTrue(
            adapter._post_mentions_bot(
                [FeishuMentionRef(name="Hermes", open_id="ou_bot", is_self=True)]
            )
        )
        self.assertFalse(
            adapter._post_mentions_bot(
                [FeishuMentionRef(name="Alice", open_id="ou_alice")]
            )
        )

class TestFeishuExtractMessageContent(unittest.TestCase):
    def _build_adapter(self):
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter.__new__(FeishuAdapter)
        adapter._bot_open_id = "ou_bot"
        adapter._bot_user_id = ""
        adapter._bot_name = "Hermes"
        adapter._download_feishu_message_resources = AsyncMock(return_value=([], []))
        return adapter

    def test_returns_six_tuple_with_mentions(self):
        adapter = self._build_adapter()
        message = SimpleNamespace(
            content=json.dumps({"text": "@_user_1 hello"}),
            message_type="text",
            message_id="m1",
            mentions=[
                SimpleNamespace(
                    key="@_user_1",
                    id=SimpleNamespace(open_id="ou_alice", user_id=""),
                    name="Alice",
                )
            ],
        )

        text, _inbound_type, _media_urls, _media_types, media_text_inlined, mentions = asyncio.run(
            adapter._extract_message_content(message)
        )
        self.assertEqual(text, "@Alice hello")
        self.assertEqual(media_text_inlined, [])
        self.assertEqual(len(mentions), 1)
        self.assertEqual(mentions[0].open_id, "ou_alice")

class TestFeishuProcessInboundMessage(unittest.TestCase):
    def _build_adapter(self):
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter.__new__(FeishuAdapter)
        adapter._bot_open_id = "ou_bot"
        adapter._bot_user_id = ""
        adapter._bot_name = "Hermes"
        adapter._download_feishu_message_resources = AsyncMock(return_value=([], []))
        adapter._fetch_message_text = AsyncMock(return_value=None)
        adapter.get_chat_info = AsyncMock(return_value={"name": "Test Chat"})
        adapter._resolve_sender_profile = AsyncMock(
            return_value={"user_id": "u1", "user_name": "Alice", "user_id_alt": None}
        )
        adapter._resolve_source_chat_type = Mock(return_value="group")
        adapter.build_source = Mock(return_value=SimpleNamespace(thread_id=None))
        adapter._maybe_route_delegate_interaction_message = AsyncMock(return_value=False)
        adapter._maybe_route_delegate_foreground_message = AsyncMock(return_value=False)
        adapter._dispatch_inbound_event = AsyncMock()
        return adapter

    def test_post_caption_is_preserved_when_text_attachment_is_inlined(self):
        adapter = self._build_adapter()
        adapter._download_feishu_message_resources = AsyncMock(
            return_value=(["/cache/notes.md"], ["text/markdown"])
        )
        adapter._maybe_extract_text_document = AsyncMock(
            return_value="[Content of notes.md]:\nattachment body"
        )
        message = SimpleNamespace(
            content=json.dumps({
                "title": "",
                "content": [[{"tag": "text", "text": "Please review"}]],
                "files": [{"file_key": "file_1", "file_name": "notes.md", "is_folder": False}],
            }),
            message_type="post",
            message_id="m-post-file",
            mentions=[],
            chat_id="oc_chat",
            thread_id=None,
            root_id=None,
            parent_id=None,
            upper_message_id=None,
        )

        asyncio.run(adapter._process_inbound_message(
            data={},
            message=message,
            sender_id=SimpleNamespace(open_id="ou_alice", user_id=None, union_id=None),
            chat_type="p2p",
            message_id="m-post-file",
        ))

        event = adapter._dispatch_inbound_event.await_args.args[0]
        self.assertEqual(
            event.text,
            "Please review\n[Attachment: notes.md]\n\n[Content of notes.md]:\nattachment body",
        )
        self.assertEqual(event.media_urls, ["/cache/notes.md"])
        self.assertEqual(event.media_text_inlined, [True])

    def test_non_command_message_with_mentions_injects_hint(self):
        from gateway.platforms.event import MessageType

        adapter = self._build_adapter()
        alice = SimpleNamespace(
            key="@_user_1",
            id=SimpleNamespace(open_id="ou_alice", user_id=""),
            name="Alice",
        )
        bob = SimpleNamespace(
            key="@_user_2",
            id=SimpleNamespace(open_id="ou_bob", user_id=""),
            name="Bob",
        )
        message = SimpleNamespace(
            content=json.dumps({"text": "@_user_1 @_user_2 make a group"}),
            message_type="text",
            message_id="m2",
            mentions=[alice, bob],
            chat_id="oc_chat",
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
                message_id="m2",
            )
        )
        event = adapter._dispatch_inbound_event.call_args.args[0]
        self.assertEqual(event.message_type, MessageType.TEXT)
        self.assertIn("[Mentioned: Alice (open_id=ou_alice), Bob (open_id=ou_bob)]", event.text)
        self.assertIn("@Alice @Bob make a group", event.text)

    def test_command_message_never_injects_hint(self):
        adapter = self._build_adapter()
        bot_mention = SimpleNamespace(
            key="@_user_1",
            id=SimpleNamespace(open_id="ou_bot", user_id=""),
            name="Hermes",
        )
        alice = SimpleNamespace(
            key="@_user_2",
            id=SimpleNamespace(open_id="ou_alice", user_id=""),
            name="Alice",
        )
        message = SimpleNamespace(
            content=json.dumps({"text": "@_user_1 /model @_user_2"}),
            message_type="text",
            message_id="m3",
            mentions=[bot_mention, alice],
            chat_id="oc_chat",
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
                message_id="m3",
            )
        )
        event = adapter._dispatch_inbound_event.call_args.args[0]
        self.assertNotIn("[Mentioned:", event.text)
        self.assertTrue(event.text.startswith("/model"))

    def test_regular_reply_root_id_does_not_become_thread_id(self):
        adapter = self._build_adapter()
        adapter._fetch_message_text = AsyncMock(return_value="parent text")
        message = SimpleNamespace(
            content=json.dumps({"text": "regular reply"}),
            message_type="text",
            message_id="m6",
            mentions=[],
            chat_id="oc_chat",
            parent_id=None,
            upper_message_id=None,
            root_id="om_root",
            thread_id=None,
        )

        asyncio.run(
            adapter._process_inbound_message(
                data=message,
                message=message,
                sender_id=None,
                chat_type="group",
                message_id="m6",
            )
        )

        adapter.build_source.assert_called_once()
        self.assertIsNone(adapter.build_source.call_args.kwargs["thread_id"])
        event = adapter._dispatch_inbound_event.call_args.args[0]
        self.assertEqual(event.reply_to_message_id, "om_root")
        self.assertEqual(event.reply_to_text, "parent text")

    def test_explicit_thread_id_is_preserved(self):
        adapter = self._build_adapter()
        message = SimpleNamespace(
            content=json.dumps({"text": "thread reply"}),
            message_type="text",
            message_id="m7",
            mentions=[],
            chat_id="oc_chat",
            parent_id=None,
            upper_message_id=None,
            root_id="om_root",
            thread_id="omt_thread",
        )

        asyncio.run(
            adapter._process_inbound_message(
                data=message,
                message=message,
                sender_id=None,
                chat_type="group",
                message_id="m7",
            )
        )

        adapter.build_source.assert_called_once()
        self.assertEqual(adapter.build_source.call_args.kwargs["thread_id"], "omt_thread")

    def _process_text(self, adapter, text, message_id):
        message = SimpleNamespace(
            content=json.dumps({"text": text}),
            message_type="text",
            message_id=message_id,
            mentions=[],
            chat_id="oc_chat",
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
                message_id=message_id,
            )
        )

    def test_gateway_commands_skip_the_delegate_loop(self):
        for text in ("/stop", "/new"):
            adapter = self._build_adapter()
            self._process_text(adapter, text, "m-stop")
            adapter._maybe_route_delegate_interaction_message.assert_not_awaited()
            adapter._maybe_route_delegate_foreground_message.assert_not_awaited()
            event = adapter._dispatch_inbound_event.call_args.args[0]
            self.assertEqual(event.get_command(), text[1:])

    def test_loop_controls_and_plain_text_still_reach_the_delegate_loop(self):
        for text in ("/main", "/exit", "follow up"):
            adapter = self._build_adapter()
            self._process_text(adapter, text, "m-loop")
            routed = adapter._maybe_route_delegate_foreground_message.call_args.kwargs["text"]
            self.assertEqual(routed.strip(), text)

class TestFeishuFetchMessageText(unittest.TestCase):
    def _build_adapter(self):
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter.__new__(FeishuAdapter)
        adapter._bot_open_id = "ou_bot"
        adapter._bot_user_id = ""
        adapter._bot_name = "Hermes"
        adapter._message_text_cache = OrderedDict()
        adapter._client = Mock()
        adapter._build_get_message_request = Mock(return_value=object())
        return adapter

    def test_fetch_message_text_marks_is_self_via_string_id_shape(self):
        """History-path Mention objects carry id as str + id_type; is_self must still work."""
        adapter = self._build_adapter()
        # bot_name is empty — is_self must be detected via open_id alone
        adapter._bot_name = ""

        bot_mention = SimpleNamespace(
            key="@_user_1",
            id="ou_bot",
            id_type="open_id",
            name="Hermes",
        )
        parent = SimpleNamespace(
            body=SimpleNamespace(content=json.dumps({"text": "@_user_1 hi"})),
            msg_type="text",
            mentions=[bot_mention],
        )
        response = Mock()
        response.success = Mock(return_value=True)
        response.data = SimpleNamespace(items=[parent])
        adapter._client.im.v1.message.get = Mock(return_value=response)

        # The rendered text should still have the bot name substituted.
        result = asyncio.run(adapter._fetch_message_text("m_parent"))
        self.assertEqual(result, "@Hermes hi")

class TestFeishuMentionEndToEnd(unittest.TestCase):
    """High-level scenarios from the design spec — verify the full pipeline."""

    def _build_adapter(self):
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter.__new__(FeishuAdapter)
        adapter._bot_open_id = "ou_bot"
        adapter._bot_user_id = ""
        adapter._bot_name = "Hermes"
        adapter._download_feishu_message_resources = AsyncMock(return_value=([], []))
        adapter._fetch_message_text = AsyncMock(return_value=None)
        adapter.get_chat_info = AsyncMock(return_value={"name": "Test Chat"})
        adapter._resolve_sender_profile = AsyncMock(
            return_value={"user_id": "u1", "user_name": "Alice", "user_id_alt": None}
        )
        adapter._resolve_source_chat_type = Mock(return_value="group")
        adapter.build_source = Mock(return_value=SimpleNamespace(thread_id=None))
        adapter._maybe_route_delegate_interaction_message = AsyncMock(return_value=False)
        adapter._maybe_route_delegate_foreground_message = AsyncMock(return_value=False)
        adapter._dispatch_inbound_event = AsyncMock()
        return adapter

    def _run(self, adapter, text, mentions):
        raw_mentions = [
            SimpleNamespace(
                key=m["key"],
                id=SimpleNamespace(open_id=m.get("open_id", ""), user_id=m.get("user_id", "")),
                name=m.get("name", ""),
            )
            for m in mentions
        ]
        message = SimpleNamespace(
            content=json.dumps({"text": text}),
            message_type="text",
            message_id="m",
            mentions=raw_mentions,
            chat_id="oc_chat",
            parent_id=None,
            upper_message_id=None,
            thread_id=None,
        )
        asyncio.run(
            adapter._process_inbound_message(
                data=message, message=message, sender_id=None, chat_type="group", message_id="m",
            )
        )
        return adapter._dispatch_inbound_event.call_args.args[0]

    def test_scenario_no_mentions_zero_regression(self):
        adapter = self._build_adapter()
        event = self._run(adapter, "plain message", [])
        self.assertEqual(event.text, "plain message")
        self.assertNotIn("[Mentioned:", event.text)

    def test_scenario_post_bot_plus_alice_filters_self_from_hint(self):
        """Post-type message @-ing both the bot and Alice: leading bot is
        stripped from the body, self is filtered from the [Mentioned: ...]
        hint, and Alice's real open_id is surfaced for the agent."""
        adapter = self._build_adapter()
        bot_mention = SimpleNamespace(
            key="@_user_1",
            id=SimpleNamespace(open_id="ou_bot", user_id=""),
            name="Hermes",
        )
        alice_mention = SimpleNamespace(
            key="@_user_2",
            id=SimpleNamespace(open_id="ou_alice", user_id=""),
            name="Alice",
        )
        post_content = json.dumps({
            "zh_cn": {
                "content": [[
                    {"tag": "at", "user_id": "@_user_1", "user_name": "Hermes"},
                    {"tag": "at", "user_id": "@_user_2", "user_name": "Alice"},
                    {"tag": "text", "text": " review the spec with Alice"},
                ]]
            }
        })
        message = SimpleNamespace(
            content=post_content,
            message_type="post",
            message_id="m_post_both",
            mentions=[bot_mention, alice_mention],
            chat_id="oc_chat",
            parent_id=None,
            upper_message_id=None,
            thread_id=None,
        )
        asyncio.run(
            adapter._process_inbound_message(
                data=message, message=message, sender_id=None,
                chat_type="group", message_id="m_post_both",
            )
        )
        event = adapter._dispatch_inbound_event.call_args.args[0]
        # Hint surfaces Alice; bot excluded because is_self=True.
        self.assertIn("[Mentioned: Alice (open_id=ou_alice)]", event.text)
        self.assertNotIn("Hermes (open_id=", event.text)
        # Body: leading @Hermes stripped, Alice preserved, trailing text intact.
        self.assertIn("@Alice review the spec with Alice", event.text)
        self.assertNotIn("@Hermes @Alice", event.text)


# ---------------------------------------------------------------------------
# SDK-boundary paths that used to be gated on lark-oapi (never installed in CI).
# The Feishu SDK request builders are the external boundary: fake them so the
# adapter logic around them runs on every lane.
# ---------------------------------------------------------------------------


class _FakeSdkBuilder:
    """Stands in for a lark-oapi ``*.builder()``: every setter records its value."""

    def __init__(self):
        self._fields = {}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def _set(value):
            self._fields[name] = value
            return self

        return _set

    def build(self):
        return SimpleNamespace(**self._fields)


class _FakeSdkRequestType:
    @staticmethod
    def builder():
        return _FakeSdkBuilder()


@pytest.fixture
def fake_lark_requests(monkeypatch):
    """Fake the lazily imported lark-oapi request modules and the raw tenant GET builder."""
    import sys
    import types

    import plugins.platforms.feishu.adapter as feishu_mod

    for parent in ("lark_oapi", "lark_oapi.api", "lark_oapi.api.im", "lark_oapi.api.contact"):
        if parent not in sys.modules:
            monkeypatch.setitem(sys.modules, parent, types.ModuleType(parent))
    im_v1 = types.ModuleType("lark_oapi.api.im.v1")
    for name in ("CreateMessageReactionRequest", "CreateMessageReactionRequestBody", "DeleteMessageReactionRequest"):
        setattr(im_v1, name, _FakeSdkRequestType)
    contact_v3 = types.ModuleType("lark_oapi.api.contact.v3")
    contact_v3.GetUserRequest = _FakeSdkRequestType
    monkeypatch.setitem(sys.modules, "lark_oapi.api.im.v1", im_v1)
    monkeypatch.setitem(sys.modules, "lark_oapi.api.contact.v3", contact_v3)
    monkeypatch.setattr(
        feishu_mod, "_tenant_get_request",
        lambda uri, *, queries=None: SimpleNamespace(uri=uri, queries=queries),
    )


def _plain_feishu_adapter():
    from gateway.config import PlatformConfig
    from plugins.platforms.feishu.adapter import FeishuAdapter

    return FeishuAdapter(PlatformConfig())


def _bot_info_response(open_id, bot_name):
    payload = json.dumps({"code": 0, "bot": {"bot_name": bot_name, "open_id": open_id}}).encode("utf-8")
    return SimpleNamespace(raw=SimpleNamespace(content=payload))


def test_hydrated_bot_identity_wins_over_stale_env_values(fake_lark_requests, monkeypatch):
    """#16993: /bot/v3/info runs even when FEISHU_BOT_* are configured, and the hydrated identity
    replaces the env values so a stale id from an old app registration can't break @mention gating."""
    monkeypatch.setenv("FEISHU_BOT_OPEN_ID", "ou_env")
    monkeypatch.setenv("FEISHU_BOT_NAME", "Env Hermes")
    adapter = _plain_feishu_adapter()
    assert adapter._bot_open_id == "ou_env"
    requests = []

    def _request(req):
        requests.append(req)
        return _bot_info_response("ou_hydrated", "Hydrated Hermes")

    adapter._client = SimpleNamespace(request=_request)

    asyncio.run(adapter._hydrate_bot_identity())

    assert [r.uri for r in requests] == ["/open-apis/bot/v3/info"]
    assert adapter._bot_open_id == "ou_hydrated"
    assert adapter._bot_name == "Hydrated Hermes"


def test_bot_sender_name_is_fetched_via_basic_batch_and_cached(fake_lark_requests):
    """Bot senders resolve via bot/v3/bots/basic_batch (the contact API has no bot names) with a
    repeated ``bot_ids`` query param, and the result is cached so the next lookup is free."""
    adapter = _plain_feishu_adapter()
    requests = []
    body = {"code": 0, "msg": "", "data": {"bots": {"ou_peer": {"bot_id": "ou_peer", "name": "Peer Bot"}}}}

    def _request(req):
        requests.append(req)
        return SimpleNamespace(raw=SimpleNamespace(content=json.dumps(body).encode()))

    adapter._client = SimpleNamespace(request=_request)

    assert asyncio.run(adapter._resolve_sender_name_from_api("ou_peer", is_bot=True)) == "Peer Bot"
    assert asyncio.run(adapter._resolve_sender_name_from_api("ou_peer", is_bot=True)) == "Peer Bot"

    assert len(requests) == 1, "second lookup must hit the cache"
    assert requests[0].uri == "/open-apis/bot/v3/bots/basic_batch"
    # Feishu expects repeated ?bot_ids= params, not comma-joined.
    assert requests[0].queries == [("bot_ids", "ou_peer")]


def test_human_sender_name_is_fetched_from_contact_api_and_cached(fake_lark_requests):
    adapter = _plain_feishu_adapter()
    lookups = []

    def _get(request):
        lookups.append(request)
        user = SimpleNamespace(name="Bob", display_name=None, nickname=None, en_name=None)
        return SimpleNamespace(success=lambda: True, data=SimpleNamespace(user=user))

    adapter._client = SimpleNamespace(contact=SimpleNamespace(v3=SimpleNamespace(user=SimpleNamespace(get=_get))))

    assert asyncio.run(adapter._resolve_sender_name_from_api("ou_bob")) == "Bob"
    assert asyncio.run(adapter._resolve_sender_name_from_api("ou_bob")) == "Bob"

    assert len(lookups) == 1, "second lookup must hit the cache"
    assert (lookups[0].user_id, lookups[0].user_id_type) == ("ou_bob", "open_id")


def _reaction_adapter(*, delete_success=True):
    adapter = _plain_feishu_adapter()
    tracker = SimpleNamespace(created=[], deleted=[])

    def _create(request):
        tracker.created.append(request.request_body.reaction_type["emoji_type"])
        return SimpleNamespace(success=lambda: True, data=SimpleNamespace(reaction_id="r_typing"))

    def _delete(request):
        tracker.deleted.append(request.reaction_id)
        return SimpleNamespace(success=lambda: delete_success, code=0 if delete_success else 99, msg="")

    adapter._client = SimpleNamespace(
        im=SimpleNamespace(v1=SimpleNamespace(message_reaction=SimpleNamespace(create=_create, delete=_delete)))
    )
    return adapter, tracker


def _run_processing(adapter, outcome):
    event = SimpleNamespace(message_id="om_msg")
    asyncio.run(adapter.on_processing_start(event))
    asyncio.run(adapter.on_processing_complete(event, outcome))


def test_processing_success_removes_typing_and_adds_nothing(fake_lark_requests, monkeypatch):
    monkeypatch.delenv("FEISHU_REACTIONS", raising=False)
    adapter, tracker = _reaction_adapter()
    _run_processing(adapter, ProcessingOutcome.SUCCESS)
    assert tracker.created == ["Typing"]
    assert tracker.deleted == ["r_typing"]
    assert "om_msg" not in adapter._pending_processing_reactions


def test_processing_failure_swaps_typing_for_cross_mark(fake_lark_requests, monkeypatch):
    monkeypatch.delenv("FEISHU_REACTIONS", raising=False)
    adapter, tracker = _reaction_adapter()
    _run_processing(adapter, ProcessingOutcome.FAILURE)
    assert tracker.created == ["Typing", "CrossMark"]
    assert tracker.deleted == ["r_typing"]


def test_processing_failure_skips_cross_mark_when_typing_removal_fails(fake_lark_requests, monkeypatch):
    """A Typing badge we couldn't remove must not get a CrossMark stacked next to it (the UI would
    read as both working and failed); the handle is kept for LRU eviction."""
    monkeypatch.delenv("FEISHU_REACTIONS", raising=False)
    adapter, tracker = _reaction_adapter(delete_success=False)
    _run_processing(adapter, ProcessingOutcome.FAILURE)
    assert tracker.created == ["Typing"]
    assert tracker.deleted == ["r_typing"]
    assert adapter._pending_processing_reactions["om_msg"] == "r_typing"


class TestFeishuAttachmentTopicRouting(unittest.TestCase):
    """Attachments rejected by topic routing must be re-anchored, not dropped.

    A message created with ``receive_id_type=thread_id`` is fine for text and
    cards but comes back 99992402 for every attachment kind, so a file the user
    asked for inside a Feishu topic used to be lost while the identical request
    in the main chat worked.
    """

    def _adapter(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        return FeishuAdapter(PlatformConfig())

    @staticmethod
    def _rejected():
        return SimpleNamespace(success=lambda: False, code=99992402, msg="field validation failed")

    @staticmethod
    def _accepted(message_id="om_sent"):
        return SimpleNamespace(
            success=lambda: True, code=0, data=SimpleNamespace(message_id=message_id)
        )

    def test_a_file_the_topic_rejects_is_resent_as_a_reply_inside_the_topic(self):
        adapter = self._adapter()
        adapter._send_raw_message = AsyncMock(side_effect=[self._rejected(), self._accepted()])
        adapter._fetch_last_message_in_thread = AsyncMock(return_value="om_last_in_topic")

        response = asyncio.run(
            adapter._send_attachment_message(
                chat_id="oc_chat",
                msg_type="file",
                payload=json.dumps({"file_key": "file_1"}),
                reply_to=None,
                metadata={"thread_id": "omt_topic"},
            )
        )

        self.assertTrue(response.success())
        self.assertEqual(adapter._send_raw_message.await_count, 2)
        retry = adapter._send_raw_message.await_args_list[1].kwargs
        self.assertEqual(retry["reply_to"], "om_last_in_topic")
        # The retry keeps the thread metadata, so the file lands in the topic
        # the user asked in rather than at the bottom of the chat.
        self.assertEqual(retry["metadata"], {"thread_id": "omt_topic"})

    def test_a_known_anchor_is_reused_instead_of_listing_the_topic(self):
        adapter = self._adapter()
        adapter._send_raw_message = AsyncMock(side_effect=[self._rejected(), self._accepted()])
        adapter._fetch_last_message_in_thread = AsyncMock(return_value="om_listed")

        asyncio.run(
            adapter._send_attachment_message(
                chat_id="oc_chat",
                msg_type="file",
                payload="{}",
                reply_to=None,
                metadata={"thread_id": "omt_topic", "reply_to_message_id": "om_known"},
            )
        )

        adapter._fetch_last_message_in_thread.assert_not_awaited()
        self.assertEqual(
            adapter._send_raw_message.await_args_list[1].kwargs["reply_to"], "om_known"
        )

    def test_without_an_anchor_the_file_goes_out_flat_rather_than_not_at_all(self):
        adapter = self._adapter()
        adapter._send_raw_message = AsyncMock(side_effect=[self._rejected(), self._accepted()])
        adapter._fetch_last_message_in_thread = AsyncMock(return_value=None)

        response = asyncio.run(
            adapter._send_attachment_message(
                chat_id="oc_chat",
                msg_type="file",
                payload="{}",
                reply_to=None,
                metadata={"thread_id": "omt_topic"},
            )
        )

        self.assertTrue(response.success())
        self.assertEqual(adapter._send_raw_message.await_count, 2)
        flat = adapter._send_raw_message.await_args_list[1].kwargs
        self.assertIsNone(flat["reply_to"])
        self.assertIsNone(flat["metadata"])

    def test_a_rejected_reply_still_falls_back_to_the_flat_chat(self):
        adapter = self._adapter()
        adapter._send_raw_message = AsyncMock(
            side_effect=[self._rejected(), self._rejected(), self._accepted()]
        )
        adapter._fetch_last_message_in_thread = AsyncMock(return_value="om_last_in_topic")

        response = asyncio.run(
            adapter._send_attachment_message(
                chat_id="oc_chat",
                msg_type="file",
                payload="{}",
                reply_to=None,
                metadata={"thread_id": "omt_topic"},
            )
        )

        self.assertTrue(response.success())
        self.assertEqual(adapter._send_raw_message.await_count, 3)
        self.assertIsNone(adapter._send_raw_message.await_args_list[2].kwargs["metadata"])

    def test_some_other_rejection_is_reported_rather_than_retried(self):
        adapter = self._adapter()
        denied = SimpleNamespace(success=lambda: False, code=230011, msg="no permission")
        adapter._send_raw_message = AsyncMock(return_value=denied)

        response = asyncio.run(
            adapter._send_attachment_message(
                chat_id="oc_chat",
                msg_type="file",
                payload="{}",
                reply_to=None,
                metadata={"thread_id": "omt_topic"},
            )
        )

        self.assertIs(response, denied)
        self.assertEqual(adapter._send_raw_message.await_count, 1)

    def test_outside_a_topic_there_is_nothing_to_re_anchor(self):
        adapter = self._adapter()
        rejected = self._rejected()
        adapter._send_raw_message = AsyncMock(return_value=rejected)

        response = asyncio.run(
            adapter._send_attachment_message(
                chat_id="oc_chat",
                msg_type="file",
                payload="{}",
                reply_to=None,
                metadata=None,
            )
        )

        self.assertIs(response, rejected)
        self.assertEqual(adapter._send_raw_message.await_count, 1)

    def test_a_document_asked_for_inside_a_topic_is_delivered(self):
        """End to end over the reported failure: a .txt requested in a topic."""
        adapter = self._adapter()

        class _FileAPI:
            def create(self, request):
                return SimpleNamespace(
                    success=lambda: True, data=SimpleNamespace(file_key="file_123")
                )

        adapter._client = SimpleNamespace(
            im=SimpleNamespace(v1=SimpleNamespace(file=_FileAPI()))
        )

        async def _direct(func, *args, **kwargs):
            return func(*args, **kwargs)

        adapter._send_raw_message = AsyncMock(side_effect=[self._rejected(), self._accepted()])
        adapter._fetch_last_message_in_thread = AsyncMock(return_value="om_last_in_topic")

        with tempfile.NamedTemporaryFile("wb", suffix=".txt", delete=False) as tmp:
            tmp.write(b"hello world")
            file_path = tmp.name

        try:
            with patch.object(adapter, "_run_blocking", side_effect=_direct):
                result = asyncio.run(
                    adapter.send_document(
                        chat_id="oc_chat",
                        file_path=file_path,
                        metadata={"thread_id": "omt_topic"},
                    )
                )
        finally:
            os.unlink(file_path)

        self.assertTrue(result.success)
        self.assertEqual(result.message_id, "om_sent")

    def test_an_image_gets_the_same_ladder_as_a_file(self):
        adapter = self._adapter()

        class _ImageAPI:
            def create(self, request):
                return SimpleNamespace(
                    success=lambda: True, data=SimpleNamespace(image_key="img_123")
                )

        adapter._client = SimpleNamespace(
            im=SimpleNamespace(v1=SimpleNamespace(image=_ImageAPI()))
        )

        async def _direct(func, *args, **kwargs):
            return func(*args, **kwargs)

        adapter._send_raw_message = AsyncMock(side_effect=[self._rejected(), self._accepted()])
        adapter._fetch_last_message_in_thread = AsyncMock(return_value="om_last_in_topic")

        with tempfile.NamedTemporaryFile("wb", suffix=".png", delete=False) as tmp:
            tmp.write(b"\x89PNG\r\n\x1a\n")
            image_path = tmp.name

        try:
            with patch.object(adapter, "_run_blocking", side_effect=_direct):
                result = asyncio.run(
                    adapter.send_image_file(
                        chat_id="oc_chat",
                        image_path=image_path,
                        metadata={"thread_id": "omt_topic"},
                    )
                )
        finally:
            os.unlink(image_path)

        self.assertTrue(result.success)
        self.assertEqual(adapter._send_raw_message.await_count, 2)


class TestFeishuAutoThreading(unittest.TestCase):
    def _build_adapter(self, *, resolved_chat_type: str):
        from gateway.config import Platform, PlatformConfig
        from gateway.session import SessionSource
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter.__new__(FeishuAdapter)
        adapter.config = PlatformConfig(extra={})
        adapter.platform = Platform.FEISHU
        adapter._bot_open_id = "ou_bot"
        adapter._bot_user_id = ""
        adapter._bot_name = "Hermes"
        adapter._download_feishu_message_resources = AsyncMock(return_value=([], []))
        adapter._fetch_message_text = AsyncMock(return_value=None)
        adapter.get_chat_info = AsyncMock(
            return_value={"name": "Test Chat", "type": resolved_chat_type}
        )
        adapter._resolve_sender_profile = AsyncMock(
            return_value={"user_id": "u1", "user_name": "Alice", "user_id_alt": None}
        )

        def _build_source(**kwargs):
            return SessionSource(platform=Platform.FEISHU, **kwargs)

        adapter.build_source = Mock(side_effect=_build_source)
        adapter._maybe_route_delegate_interaction_message = AsyncMock(return_value=False)
        adapter._maybe_route_delegate_foreground_message = AsyncMock(return_value=False)
        adapter._dispatch_inbound_event = AsyncMock()
        adapter._active_sessions = {}
        adapter._session_store = None
        adapter._pending_auto_thread_roots = OrderedDict()
        adapter._failed_auto_thread_roots = OrderedDict()
        return adapter

    @staticmethod
    def _message(
        message_id: str,
        *,
        thread_id=None,
        root_id=None,
        chat_id="oc_chat",
    ):
        return SimpleNamespace(
            content=json.dumps({"text": "hello"}),
            message_type="text",
            message_id=message_id,
            mentions=[],
            chat_id=chat_id,
            parent_id=root_id,
            upper_message_id=None,
            thread_id=thread_id,
            root_id=root_id,
        )

    def _dispatch(self, adapter, message, *, event_chat_type="group"):
        asyncio.run(
            adapter._process_inbound_message(
                data=message,
                message=message,
                sender_id=SimpleNamespace(open_id="ou_user"),
                chat_type=event_chat_type,
                message_id=message.message_id,
            )
        )
        return adapter._dispatch_inbound_event.call_args.args[0]

    def test_top_level_group_and_dm_messages_use_message_id_as_session_thread(self):
        from gateway.session import build_session_key

        cases = (("group", "group", "oc_chat"), ("dm", "p2p", "ou_dm_chat"))
        keys = []
        for source_type, event_type, chat_id in cases:
            with self.subTest(chat_type=source_type), patch.dict(os.environ, {}, clear=True):
                adapter = self._build_adapter(resolved_chat_type=source_type)
                event = self._dispatch(
                    adapter,
                    self._message("om_root_1", chat_id=chat_id),
                    event_chat_type=event_type,
                )
                self.assertEqual(event.source.thread_id, "om_root_1")
                self.assertIn("om_root_1", adapter._pending_auto_thread_roots)
                keys.append(build_session_key(event.source))
        self.assertNotEqual(keys[0], keys[1])

    def test_different_top_level_messages_create_different_sessions(self):
        from gateway.session import build_session_key

        adapter = self._build_adapter(resolved_chat_type="group")
        with patch.dict(os.environ, {}, clear=True):
            first = self._dispatch(adapter, self._message("om_root_1"))
            adapter._dispatch_inbound_event.reset_mock()
            second = self._dispatch(adapter, self._message("om_root_2"))
        self.assertNotEqual(build_session_key(first.source), build_session_key(second.source))

    def test_forum_and_disabled_flag_preserve_flat_messages(self):
        forum = self._build_adapter(resolved_chat_type="forum")
        with patch.dict(os.environ, {}, clear=True):
            forum_event = self._dispatch(forum, self._message("om_forum"))
        self.assertIsNone(forum_event.source.thread_id)

        cases = (("group", "group", "oc_chat"), ("dm", "p2p", "ou_dm_chat"))
        for source_type, event_type, chat_id in cases:
            with self.subTest(chat_type=source_type), patch.dict(
                os.environ, {"FEISHU_REPLY_THREAD": "false"}, clear=True
            ):
                adapter = self._build_adapter(resolved_chat_type=source_type)
                event = self._dispatch(
                    adapter,
                    self._message("om_flat", chat_id=chat_id),
                    event_chat_type=event_type,
                )
                self.assertIsNone(event.source.thread_id)

    def test_follow_up_reuses_active_root_session(self):
        from gateway.session import SessionSource, build_session_key

        adapter = self._build_adapter(resolved_chat_type="group")
        candidate = SessionSource(
            platform=adapter.platform,
            chat_id="oc_chat",
            chat_name="Test Chat",
            chat_type="group",
            user_id="u1",
            user_name="Alice",
            thread_id="om_root_1",
            message_id="om_child",
        )
        root_key = build_session_key(candidate)
        adapter._active_sessions[root_key] = asyncio.Event()

        event = self._dispatch(
            adapter,
            self._message(
                "om_child",
                thread_id="omt_real_thread",
                root_id="om_root_1",
            ),
        )
        self.assertEqual(event.source.thread_id, "om_root_1")
        self.assertEqual(build_session_key(event.source), root_key)

    def test_follow_up_reuses_persisted_root_session_in_dm(self):
        from gateway.session import SessionSource, build_session_key

        adapter = self._build_adapter(resolved_chat_type="dm")
        candidate = SessionSource(
            platform=adapter.platform,
            chat_id="ou_dm_chat",
            chat_name="Test Chat",
            chat_type="dm",
            user_id="u1",
            user_name="Alice",
            thread_id="om_dm_root",
            message_id="om_dm_child",
        )
        root_key = build_session_key(candidate)
        adapter._session_store = SimpleNamespace(
            peek_session_id=Mock(side_effect=lambda key: "session-1" if key == root_key else None)
        )

        event = self._dispatch(
            adapter,
            self._message(
                "om_dm_child",
                thread_id="omt_dm_thread",
                root_id="om_dm_root",
                chat_id="ou_dm_chat",
            ),
            event_chat_type="p2p",
        )
        self.assertEqual(event.source.thread_id, "om_dm_root")
        self.assertEqual(build_session_key(event.source), root_key)

    def test_human_thread_without_root_session_keeps_real_thread_id(self):
        adapter = self._build_adapter(resolved_chat_type="group")
        event = self._dispatch(
            adapter,
            self._message(
                "om_child",
                thread_id="omt_human_thread",
                root_id="om_human_root",
            ),
        )
        self.assertEqual(event.source.thread_id, "omt_human_thread")

    def test_quote_reply_and_its_topic_follow_up_share_one_session(self):
        # A main-timeline quote reply carries root_id (the quoted message) but
        # no thread_id. The bot's reply roots the topic on the quote reply
        # itself, so later topic messages arrive with root_id = the quote reply.
        from gateway.session import build_session_key

        adapter = self._build_adapter(resolved_chat_type="group")
        with patch.dict(os.environ, {}, clear=True):
            first = self._dispatch(
                adapter, self._message("om_quote_reply", root_id="om_quoted")
            )
        first_key = build_session_key(first.source)
        self.assertEqual(first.source.thread_id, "om_quote_reply")
        self.assertIn("om_quote_reply", adapter._pending_auto_thread_roots)
        self.assertEqual(first.reply_to_message_id, "om_quoted")

        follow_up_adapter = self._build_adapter(resolved_chat_type="group")
        follow_up_adapter._session_store = SimpleNamespace(
            peek_session_id=Mock(side_effect=lambda key: "session-1" if key == first_key else None)
        )
        with patch.dict(os.environ, {}, clear=True):
            follow_up = self._dispatch(
                follow_up_adapter,
                self._message("om_in_topic", thread_id="omt_topic", root_id="om_quote_reply"),
            )
        self.assertEqual(build_session_key(follow_up.source), first_key)

    def test_quote_reply_with_reply_thread_disabled_stays_flat(self):
        # v0.4 keys a quote reply on no topic at all (#20548); the fork only adds topics when on.
        adapter = self._build_adapter(resolved_chat_type="group")
        with patch.dict(os.environ, {"FEISHU_REPLY_THREAD": "false"}, clear=True):
            event = self._dispatch(
                adapter, self._message("om_quote_reply", root_id="om_quoted")
            )
        self.assertIsNone(event.source.thread_id)
        self.assertNotIn("om_quoted", adapter._pending_auto_thread_roots)

    def test_quote_reply_approval_card_lands_under_the_quote_reply(self):
        # Approval cards anchor on the session thread_id rather than on the
        # triggering message, so a session keyed on the quoted message would
        # open a second topic under it.
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        router = self._build_adapter(resolved_chat_type="group")
        with patch.dict(os.environ, {}, clear=True):
            event = self._dispatch(
                router, self._message("om_quote_reply", root_id="om_quoted")
            )

        captured = {}

        class _MessageAPI:
            def reply(self, request):
                captured["request"] = request
                return SimpleNamespace(
                    success=lambda: True,
                    data=SimpleNamespace(message_id="om_card"),
                )

        sender = FeishuAdapter(PlatformConfig())
        sender._client = SimpleNamespace(
            im=SimpleNamespace(v1=SimpleNamespace(message=_MessageAPI()))
        )

        async def _direct(func, *args, **kwargs):
            return func(*args, **kwargs)

        with patch.object(sender, "_run_blocking", side_effect=_direct):
            result = asyncio.run(
                sender.send_exec_approval(
                    chat_id="oc_chat",
                    command="rm -rf /tmp/x",
                    session_key="session-key",
                    metadata={"thread_id": event.source.thread_id},
                )
            )

        self.assertTrue(result.success)
        self.assertEqual(captured["request"].message_id, "om_quote_reply")
        self.assertTrue(captured["request"].request_body.reply_in_thread)


class TestFeishuAutoThreadSending(unittest.TestCase):
    def _build_adapter(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        return adapter

    def test_root_thread_metadata_replies_to_root_and_sets_thread_flag(self):
        adapter = self._build_adapter()
        captured = {}

        class _MessageAPI:
            def reply(self, request):
                captured["request"] = request
                return SimpleNamespace(
                    success=lambda: True,
                    data=SimpleNamespace(message_id="om_reply"),
                )

        adapter._client = SimpleNamespace(
            im=SimpleNamespace(v1=SimpleNamespace(message=_MessageAPI()))
        )

        async def _direct(func, *args, **kwargs):
            return func(*args, **kwargs)

        with patch.object(adapter, "_run_blocking", side_effect=_direct):
            response = asyncio.run(
                adapter._send_raw_message(
                    chat_id="oc_chat",
                    msg_type="text",
                    payload=json.dumps({"text": "hello"}),
                    reply_to=None,
                    metadata={"thread_id": "om_root_1"},
                )
            )

        self.assertTrue(response.success())
        self.assertEqual(captured["request"].message_id, "om_root_1")
        self.assertTrue(captured["request"].request_body.reply_in_thread)

    def test_auto_thread_failure_falls_back_once_without_thread_metadata(self):
        adapter = self._build_adapter()
        adapter._mark_auto_thread_pending("om_root_1")
        failed = SimpleNamespace(success=lambda: False, code=230011)
        succeeded = SimpleNamespace(success=lambda: True, code=0)
        adapter._send_raw_message = AsyncMock(side_effect=[failed, succeeded])

        result = asyncio.run(
            adapter._feishu_send_with_retry(
                chat_id="oc_chat",
                msg_type="text",
                payload="payload",
                reply_to="om_root_1",
                metadata={"thread_id": "om_root_1"},
            )
        )

        self.assertIs(result, succeeded)
        self.assertEqual(adapter._send_raw_message.await_count, 2)
        fallback = adapter._send_raw_message.await_args_list[1].kwargs
        self.assertIsNone(fallback["reply_to"])
        self.assertIsNone(fallback["metadata"])
        self.assertIn("om_root_1", adapter._failed_auto_thread_roots)

    def test_later_output_stays_flat_after_auto_thread_failure(self):
        adapter = self._build_adapter()
        adapter._mark_auto_thread_failed("om_root_1")
        succeeded = SimpleNamespace(success=lambda: True, code=0)
        adapter._send_raw_message = AsyncMock(return_value=succeeded)

        result = asyncio.run(
            adapter._feishu_send_with_retry(
                chat_id="oc_chat",
                msg_type="text",
                payload="payload",
                reply_to="om_root_1",
                metadata={"thread_id": "om_root_1"},
            )
        )

        self.assertIs(result, succeeded)
        call = adapter._send_raw_message.await_args_list[-1].kwargs
        self.assertIsNone(call["reply_to"])
        self.assertIsNone(call["metadata"])

    def test_invalid_post_keeps_auto_thread_pending_for_plain_text_retry(self):
        adapter = self._build_adapter()
        adapter._mark_auto_thread_pending("om_root_1")
        failed = SimpleNamespace(
            success=lambda: False,
            code=230001,
            msg="content format of the post type is incorrect",
        )
        adapter._send_raw_message = AsyncMock(return_value=failed)

        result = asyncio.run(
            adapter._feishu_send_with_retry(
                chat_id="oc_chat",
                msg_type="post",
                payload="payload",
                reply_to="om_root_1",
                metadata={"thread_id": "om_root_1"},
            )
        )

        self.assertIs(result, failed)
        self.assertEqual(adapter._send_raw_message.await_count, 1)
        self.assertIn("om_root_1", adapter._pending_auto_thread_roots)
        self.assertNotIn("om_root_1", adapter._failed_auto_thread_roots)

    def test_real_thread_failure_never_falls_back_to_top_level(self):
        adapter = self._build_adapter()
        failed = SimpleNamespace(success=lambda: False, code=230011)
        adapter._send_raw_message = AsyncMock(return_value=failed)

        result = asyncio.run(
            adapter._feishu_send_with_retry(
                chat_id="oc_chat",
                msg_type="text",
                payload="payload",
                reply_to="om_child",
                metadata={"thread_id": "omt_real_thread"},
            )
        )

        self.assertIs(result, failed)
        self.assertEqual(adapter._send_raw_message.await_count, 1)

    def test_root_thread_id_is_already_a_valid_media_reply_anchor(self):
        adapter = self._build_adapter()
        adapter._client = Mock()
        self.assertEqual(
            asyncio.run(adapter._fetch_last_message_in_thread("om_root_1")),
            "om_root_1",
        )
        self.assertFalse(adapter._client.im.v1.message.list.called)

    # -- failures that do not rule a topic out -------------------------------

    _CARD_PAYLOAD = json.dumps({"type": "card", "data": {"card_id": "card_1"}})

    def _send_pending(self, adapter, *, msg_type="text", payload="payload"):
        # The in-topic retry backs off; the tests do not need to wait for it.
        with patch("plugins.platforms.feishu.adapter.asyncio.sleep", new=AsyncMock()):
            return asyncio.run(
                adapter._feishu_send_with_retry(
                    chat_id="oc_chat",
                    msg_type=msg_type,
                    payload=payload,
                    reply_to="om_root_1",
                    metadata={"thread_id": "om_root_1"},
                )
            )

    def _assert_all_sends_in_topic(self, adapter):
        for call in adapter._send_raw_message.await_args_list:
            self.assertEqual(call.kwargs["metadata"], {"thread_id": "om_root_1"})

    def test_internal_error_is_retried_in_the_topic_once_it_exists(self):
        adapter = self._build_adapter()
        adapter._mark_auto_thread_pending("om_root_1")
        failed = SimpleNamespace(success=lambda: False, code=2200, msg="internal error")
        succeeded = SimpleNamespace(success=lambda: True, code=0)
        adapter._send_raw_message = AsyncMock(side_effect=[failed, succeeded])
        adapter._auto_thread_topic_exists = AsyncMock(return_value=True)

        result = self._send_pending(adapter)

        self.assertIs(result, succeeded)
        self.assertEqual(adapter._send_raw_message.await_count, 2)
        self._assert_all_sends_in_topic(adapter)
        self.assertNotIn("om_root_1", adapter._pending_auto_thread_roots)
        self.assertNotIn("om_root_1", adapter._failed_auto_thread_roots)

    def test_one_failure_without_a_topic_is_retried_in_the_topic(self):
        adapter = self._build_adapter()
        adapter._mark_auto_thread_pending("om_root_1")
        failed = SimpleNamespace(success=lambda: False, code=230020, msg="rate limited")
        succeeded = SimpleNamespace(success=lambda: True, code=0)
        adapter._send_raw_message = AsyncMock(side_effect=[failed, succeeded])
        adapter._auto_thread_topic_exists = AsyncMock(return_value=False)

        result = self._send_pending(adapter)

        self.assertIs(result, succeeded)
        self.assertEqual(adapter._send_raw_message.await_count, 2)
        self._assert_all_sends_in_topic(adapter)
        self.assertNotIn("om_root_1", adapter._pending_auto_thread_roots)
        self.assertNotIn("om_root_1", adapter._failed_auto_thread_roots)

    def test_second_failure_without_a_topic_falls_back_flat_once(self):
        adapter = self._build_adapter()
        adapter._mark_auto_thread_pending("om_root_1")
        failed = SimpleNamespace(success=lambda: False, code=2200, msg="internal error")
        succeeded = SimpleNamespace(success=lambda: True, code=0)
        adapter._send_raw_message = AsyncMock(side_effect=[failed, failed, succeeded])
        adapter._auto_thread_topic_exists = AsyncMock(return_value=False)

        result = self._send_pending(adapter)

        self.assertIs(result, succeeded)
        calls = adapter._send_raw_message.await_args_list
        self.assertEqual(len(calls), 3)
        for call in calls[:2]:
            self.assertEqual(call.kwargs["metadata"], {"thread_id": "om_root_1"})
        self.assertIsNone(calls[2].kwargs["reply_to"])
        self.assertIsNone(calls[2].kwargs["metadata"])
        self.assertIn("om_root_1", adapter._failed_auto_thread_roots)

    def test_errors_in_the_topic_retry_are_not_counted_twice(self):
        adapter = self._build_adapter()
        adapter._mark_auto_thread_pending("om_root_1")
        failed = SimpleNamespace(success=lambda: False, code=2200, msg="internal error")
        succeeded = SimpleNamespace(success=lambda: True, code=0)
        timeout = RuntimeError("timed out")
        adapter._send_raw_message = AsyncMock(
            side_effect=[failed, timeout, timeout, timeout, succeeded]
        )
        adapter._auto_thread_topic_exists = AsyncMock(return_value=False)

        result = self._send_pending(adapter)

        # One failed reply, one retry that exhausts its own attempts, then the
        # single flat message — not a second round of attempts on top.
        self.assertIs(result, succeeded)
        calls = adapter._send_raw_message.await_args_list
        self.assertEqual(len(calls), 5)
        self.assertIsNone(calls[-1].kwargs["metadata"])
        self.assertIn("om_root_1", adapter._failed_auto_thread_roots)

    def test_group_without_topic_support_falls_back_flat_at_once(self):
        for code in (230071, 230072):
            with self.subTest(code=code):
                adapter = self._build_adapter()
                adapter._mark_auto_thread_pending("om_root_1")
                failed = SimpleNamespace(success=lambda: False, code=code)
                succeeded = SimpleNamespace(success=lambda: True, code=0)
                adapter._send_raw_message = AsyncMock(side_effect=[failed, succeeded])
                adapter._auto_thread_topic_exists = AsyncMock(return_value=False)

                result = self._send_pending(adapter)

                self.assertIs(result, succeeded)
                self.assertIsNone(adapter._send_raw_message.await_args_list[1].kwargs["metadata"])
                self.assertIn("om_root_1", adapter._failed_auto_thread_roots)
                adapter._auto_thread_topic_exists.assert_not_awaited()

    def test_card_entity_is_never_resent_by_the_flat_fallback(self):
        cases = (
            SimpleNamespace(success=lambda: False, code=230071),
            SimpleNamespace(success=lambda: False, code=2200, msg="internal error"),
        )
        for failed in cases:
            with self.subTest(code=failed.code):
                adapter = self._build_adapter()
                adapter._mark_auto_thread_pending("om_root_1")
                adapter._send_raw_message = AsyncMock(return_value=failed)
                adapter._auto_thread_topic_exists = AsyncMock(return_value=False)

                for _ in range(3):
                    result = self._send_pending(
                        adapter, msg_type="interactive", payload=self._CARD_PAYLOAD
                    )

                self.assertIs(result, failed)
                # One attempt per call — the entity is never sent a second time.
                self.assertEqual(adapter._send_raw_message.await_count, 3)

    def test_card_entity_raising_after_retries_is_not_resent_flat(self):
        adapter = self._build_adapter()
        adapter._mark_auto_thread_pending("om_root_1")
        adapter._send_raw_message = AsyncMock(side_effect=RuntimeError("timed out"))
        adapter._auto_thread_topic_exists = AsyncMock(return_value=False)

        with self.assertRaises(RuntimeError):
            self._send_pending(adapter, msg_type="interactive", payload=self._CARD_PAYLOAD)

        for call in adapter._send_raw_message.await_args_list:
            self.assertEqual(call.kwargs["metadata"], {"thread_id": "om_root_1"})
        self.assertIn("om_root_1", adapter._pending_auto_thread_roots)

    def test_failure_log_names_the_code_message_and_log_id(self):
        adapter = self._build_adapter()
        adapter._mark_auto_thread_pending("om_root_1")
        failed = SimpleNamespace(
            success=lambda: False,
            code=2200,
            msg="internal error",
            get_log_id=lambda: "log_abc",
        )
        succeeded = SimpleNamespace(success=lambda: True, code=0)
        adapter._send_raw_message = AsyncMock(side_effect=[failed, succeeded])
        adapter._auto_thread_topic_exists = AsyncMock(return_value=False)

        with self.assertLogs(level="WARNING") as logs:
            self._send_pending(adapter)

        line = "\n".join(logs.output)
        self.assertIn("code=2200", line)
        self.assertIn("internal error", line)
        self.assertIn("log_id=log_abc", line)
        self.assertIn("reply_to=om_root_1", line)

    def test_card_block_handle_is_never_a_reply_target(self):
        adapter = self._build_adapter()
        succeeded = SimpleNamespace(success=lambda: True, code=0)
        adapter._send_raw_message = AsyncMock(return_value=succeeded)

        asyncio.run(
            adapter._feishu_send_with_retry(
                chat_id="oc_chat",
                msg_type="text",
                payload="payload",
                reply_to="hermes-card:card_1:0:abcd1234",
                metadata={"thread_id": "omt_real_thread"},
            )
        )

        call = adapter._send_raw_message.await_args.kwargs
        self.assertIsNone(call["reply_to"])
        self.assertEqual(call["metadata"], {"thread_id": "omt_real_thread"})

    def test_topic_probe_reads_the_root_messages_thread_id(self):
        adapter = self._build_adapter()

        async def _direct(func, *args, **kwargs):
            return func(*args, **kwargs)

        def _client_returning(response):
            return SimpleNamespace(
                im=SimpleNamespace(
                    v1=SimpleNamespace(message=SimpleNamespace(get=lambda request: response))
                )
            )

        def _root(thread_id):
            return SimpleNamespace(
                success=lambda: True,
                data=SimpleNamespace(items=[SimpleNamespace(thread_id=thread_id)]),
            )

        cases = (
            (_root("omt_new_topic"), True),
            (_root(None), False),
            (SimpleNamespace(success=lambda: False, code=231003), False),
        )
        for response, expected in cases:
            with self.subTest(expected=expected):
                adapter._client = _client_returning(response)
                with patch.object(adapter, "_run_blocking", side_effect=_direct):
                    self.assertIs(
                        asyncio.run(adapter._auto_thread_topic_exists("om_root_1")),
                        expected,
                    )


@unittest.skipUnless(_HAS_LARK_OAPI, "lark-oapi not installed")
class TestSenderNameResolution(unittest.TestCase):
    """Tests for _resolve_sender_name_from_api (contact API + cache)."""


    @patch.dict(os.environ, {}, clear=True)
    def test_returns_cached_name_within_ttl(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        adapter._client = SimpleNamespace()
        future_expire = time.time() + 600
        adapter._sender_name_cache["ou_cached"] = ("Alice", future_expire)
        result = asyncio.run(adapter._resolve_sender_name_from_api("ou_cached"))
        self.assertEqual(result, "Alice")

    @patch.dict(os.environ, {}, clear=True)
    def test_fetches_and_caches_name_from_api(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        user_obj = SimpleNamespace(name="Bob", display_name=None, nickname=None, en_name=None)
        mock_response = SimpleNamespace(
            success=lambda: True,
            data=SimpleNamespace(user=user_obj),
        )

        async def _direct(func, *args, **kwargs):
            return func(*args, **kwargs)

        class _ContactAPI:
            def get(self, request):
                return mock_response

        adapter._client = SimpleNamespace(
            contact=SimpleNamespace(v3=SimpleNamespace(user=_ContactAPI()))
        )

        with patch("plugins.platforms.feishu.adapter.asyncio.to_thread", side_effect=_direct):
            result = asyncio.run(adapter._resolve_sender_name_from_api("ou_bob"))

        self.assertEqual(result, "Bob")
        self.assertIn("ou_bob", adapter._sender_name_cache)

    @patch.dict(os.environ, {}, clear=True)
    def test_sender_profile_prefers_open_id_and_caches_all_identity_aliases(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        adapter._resolve_sender_name_from_api = AsyncMock(return_value="Ada")
        sender_id = SimpleNamespace(
            open_id="ou_ada",
            user_id="legacy_user_id",
            union_id="on_ada",
        )

        profile = asyncio.run(adapter._resolve_sender_profile(sender_id))

        self.assertEqual(profile, {
            "user_id": "legacy_user_id",
            "user_name": "Ada",
            "user_id_alt": "on_ada",
        })
        adapter._resolve_sender_name_from_api.assert_awaited_once_with("ou_ada", is_bot=False)
        assert {"ou_ada", "legacy_user_id", "on_ada"} <= set(adapter._sender_name_cache)

    @patch.dict(os.environ, {}, clear=True)
    def test_expired_cache_triggers_new_api_call(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())
        # Expired cache entry.
        adapter._sender_name_cache["ou_expired"] = ("OldName", time.time() - 1)

        async def _direct(func, *args, **kwargs):
            return func(*args, **kwargs)

        user_obj = SimpleNamespace(name="NewName", display_name=None, nickname=None, en_name=None)

        class _ContactAPI:
            def get(self, request):
                return SimpleNamespace(success=lambda: True, data=SimpleNamespace(user=user_obj))

        adapter._client = SimpleNamespace(
            contact=SimpleNamespace(v3=SimpleNamespace(user=_ContactAPI()))
        )

        with patch("plugins.platforms.feishu.adapter.asyncio.to_thread", side_effect=_direct):
            result = asyncio.run(adapter._resolve_sender_name_from_api("ou_expired"))

        self.assertEqual(result, "NewName")

    @patch.dict(os.environ, {}, clear=True)
    def test_api_failure_returns_none_without_raising(self):
        from gateway.config import PlatformConfig
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = FeishuAdapter(PlatformConfig())

        class _BrokenContactAPI:
            def get(self, _request):
                raise RuntimeError("API down")

        adapter._client = SimpleNamespace(
            contact=SimpleNamespace(v3=SimpleNamespace(user=_BrokenContactAPI()))
        )

        async def _direct(func, *args, **kwargs):
            return func(*args, **kwargs)

        with patch("plugins.platforms.feishu.adapter.asyncio.to_thread", side_effect=_direct):
            result = asyncio.run(adapter._resolve_sender_name_from_api("ou_broken"))

        self.assertIsNone(result)

