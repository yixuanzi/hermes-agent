"""The fork's Feishu features (Aegis), mixed into ``FeishuAdapter`` as one base.

Each feature lives in its own sibling (delegate I/O, automatic topics, admission and history, card
output, card media); this module composes them and owns the few seams the adapter calls: state and
settings at construction, the Jev admission gate, inbound routing between ``build_source`` and
dispatch, and the pending-topic send path.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass, fields
from typing import Any, Dict, Optional, Tuple

from gateway.platforms.base import DelegateLoopAdapterMixin, MessageType, is_gateway_command_during_delegate_loop
from plugins.platforms.feishu.adapter_aegis_admission import (
    _DEFAULT_GROUP_HISTORY_CLI_BIN,
    _DEFAULT_GROUP_HISTORY_HOURS,
    _DEFAULT_GROUP_HISTORY_LIMIT,
    _DEFAULT_GROUP_HISTORY_THREAD_LIMIT,
    _FEISHU_GROUP_HISTORY_MSG_MAX_CHARS,
    _FEISHU_GROUP_HISTORY_PAGE_SIZE_MAX,
    FeishuAdmissionMixin,
    _coerce_positive_float,
    _env_boolean_default_false,
    _group_history,
)
from plugins.platforms.feishu.adapter_aegis_card_media import FeishuCardMediaMixin
from plugins.platforms.feishu.adapter_aegis_cards import (
    _DEFAULT_CARD_FLUSH_INTERVAL,
    FeishuCardOutputMixin,
    _card_copy_from_env,
    _cardkit_module,
)
from plugins.platforms.feishu.adapter_aegis_delegate import FeishuDelegateMixin
from plugins.platforms.feishu.adapter_aegis_topics import FeishuAutoTopicMixin, _env_boolean_default_true
from plugins.platforms.feishu.adapter_choice_cards import FeishuChoiceCardsMixin
from utils import env_float

_DELEGATE_LOOP_CONTROLS = frozenset({"/main", "/exit"})


def _fa():
    import sys

    return sys.modules["plugins.platforms.feishu.adapter"]


def _setting(extra: dict[str, Any], key: str, env: str) -> Any:
    """``config.yaml`` (``platforms.feishu.extra``) wins over the environment."""
    return extra[key] if key in extra else os.getenv(env)


def _history_int(extra, key, env, *, default, min_value, cap=None):
    value = _fa()._coerce_required_int(_setting(extra, key, env), default=default, min_value=min_value)
    return min(value, cap) if cap is not None else value


@dataclass(frozen=True)
class AegisFeishuSettings:
    """The fork's Feishu settings: ``config.yaml`` (``platforms.feishu.extra``) wins over the env."""

    reply_thread: bool = True
    # Render a turn as one CardKit card; opt-in, text/post stays the default and the fallback.
    card_output: bool = False
    # Prepend recent group messages to the first turn of a new group session; opt-in.
    group_history_enabled: bool = False
    group_history_limit: int = _DEFAULT_GROUP_HISTORY_LIMIT
    group_history_hours: float = _DEFAULT_GROUP_HISTORY_HOURS
    group_history_msg_max_chars: int = _FEISHU_GROUP_HISTORY_MSG_MAX_CHARS  # 0 disables truncation
    group_history_thread_limit: int = _DEFAULT_GROUP_HISTORY_THREAD_LIMIT  # 0 disables the topic fetch
    group_history_source: str = "api"  # "api" (lark SDK) or "cli" (lark-cli renders card bodies)
    group_history_cli_bin: str = _DEFAULT_GROUP_HISTORY_CLI_BIN
    group_history_card_regions: tuple[str, ...] = ()

    def adapter_attributes(self) -> dict[str, Any]:
        """The adapter attribute each setting is read from by the feature code."""
        names = {"reply_thread": "_reply_thread_enabled_setting", "card_output": "_card_output_enabled"}
        return {names.get(f.name, f"_{f.name}"): getattr(self, f.name) for f in fields(self)}


def load_aegis_settings(extra: dict[str, Any]) -> AegisFeishuSettings:
    to_bool = _fa()._to_boolean
    return AegisFeishuSettings(
        reply_thread=_env_boolean_default_true("FEISHU_REPLY_THREAD"),
        card_output=(to_bool(extra["card_output"]) if "card_output" in extra
                     else _env_boolean_default_false("FEISHU_CARD_OUTPUT")),
        group_history_enabled=(to_bool(extra["group_history_enabled"]) if "group_history_enabled" in extra
                               else _env_boolean_default_false("FEISHU_GROUP_HISTORY")),
        group_history_limit=_history_int(
            extra, "group_history_limit", "FEISHU_GROUP_HISTORY_LIMIT", default=_DEFAULT_GROUP_HISTORY_LIMIT,
            min_value=1, cap=_FEISHU_GROUP_HISTORY_PAGE_SIZE_MAX),
        group_history_hours=_coerce_positive_float(
            _setting(extra, "group_history_hours", "FEISHU_GROUP_HISTORY_HOURS"), default=_DEFAULT_GROUP_HISTORY_HOURS),
        group_history_msg_max_chars=_history_int(
            extra, "group_history_msg_max_chars", "FEISHU_GROUP_HISTORY_MSG_MAX_CHARS",
            default=_FEISHU_GROUP_HISTORY_MSG_MAX_CHARS, min_value=0),
        group_history_thread_limit=_history_int(
            extra, "group_history_thread_limit", "FEISHU_GROUP_HISTORY_THREAD_LIMIT",
            default=_DEFAULT_GROUP_HISTORY_THREAD_LIMIT, min_value=0, cap=_FEISHU_GROUP_HISTORY_PAGE_SIZE_MAX),
        group_history_source=_group_history.normalize_history_source(
            _setting(extra, "group_history_source", "FEISHU_GROUP_HISTORY_SOURCE")),
        group_history_cli_bin=(str(extra.get("group_history_cli_bin") or "").strip()
                               or str(os.getenv("FEISHU_GROUP_HISTORY_CLI_BIN") or "").strip()
                               or _DEFAULT_GROUP_HISTORY_CLI_BIN),
        group_history_card_regions=_group_history.normalize_card_regions(
            _setting(extra, "group_history_card_regions", "FEISHU_GROUP_HISTORY_CARD_REGIONS")),
    )


class FeishuAegisMixin(
    FeishuDelegateMixin, FeishuAutoTopicMixin, FeishuAdmissionMixin, FeishuCardOutputMixin, FeishuCardMediaMixin,
    DelegateLoopAdapterMixin,
):
    """The fork's Feishu features; listed ahead of the upstream mixins on ``FeishuAdapter``."""

    # Remote A2A interaction clicks carry ``hermes_delegate_kind``; every other route is upstream's.
    _CARD_ACTION_ROUTES = (("hermes_delegate_kind", "_handle_delegate_card_action"),
                           *FeishuChoiceCardsMixin._CARD_ACTION_ROUTES)

    def _init_aegis_state(self) -> None:
        """Construction-time state and settings for the fork's features."""
        for name, value in load_aegis_settings(self.config.extra or {}).adapter_attributes().items():
            setattr(self, name, value)
        # Remote A2A interaction cards stay outside the local approval/clarify maps; their
        # responder is an authenticated A2A bridge owned by the delegate client.
        self._delegate_interactions: dict[str, dict[str, Any]] = {}
        self._DELEGATE_INTERACTIONS_MAX = 1000
        # Foreground A2A routes and stream state are process-local by design.
        self._delegate_routes: dict[str, Any] = {}
        self._delegate_routes_lock = threading.RLock()
        self._delegate_stream_states: dict[str, Any] = {}
        self._delegate_loop: Optional[asyncio.AbstractEventLoop] = None
        # A top-level message is a prospective topic only until its first threaded reply succeeds;
        # failed roots stay flat for the rest of the turn; soft failures wait for a second strike.
        self._pending_auto_thread_roots: OrderedDict[str, None] = OrderedDict()
        self._failed_auto_thread_roots: OrderedDict[str, None] = OrderedDict()
        self._auto_thread_soft_failures: OrderedDict[str, int] = OrderedDict()
        # One live card per route, sealed whenever the speaker changes (main agent ↔ delegate).
        self._card_copy = _card_copy_from_env()
        self._card_manager = _cardkit_module().FeishuCardOutputManager(
            self, copy=self._card_copy,
            flush_interval=env_float("HERMES_FEISHU_CARD_FLUSH_INTERVAL", _DEFAULT_CARD_FLUSH_INTERVAL))
        # Per-delegate body accumulation (owner → {block_id, text}), its locks, the owners that
        # streamed during the current exchange, and the per-route ledger of files already sent.
        self._delegate_card_blocks: dict[str, dict[str, Any]] = {}
        self._delegate_card_locks: dict[str, asyncio.Lock] = {}
        self._delegate_card_streamed: set = set()
        self._card_media_dispatched: dict[str, OrderedDict[str, None]] = {}

    # --- Jev admission gate (called from _handle_message_event_data) ---

    def _jev_gate(self, reason: Optional[str], sender: Any, message: Any) -> tuple[Optional[str], bool]:
        """``(reason, jev_gate_pending)`` for an inbound event after ``_admit``.

        Jev judges any unaddressed human group message whose sender the group admits: one the mention
        gate was about to drop, or one a ``require_mention: false`` group admits outright. Bot senders
        (bot-to-bot loops) never qualify; the config read comes before ``_mentions_self``, so a
        disabled gate costs nothing. ``judge`` admits the message pending the verdict; a master switch
        with nothing able to vouch for the message drops it (``jev_gate_unavailable``).
        """
        if reason not in (None, "group_policy_rejected") or getattr(message, "chat_type", "p2p") == "p2p":
            return reason, False
        fa = _fa()
        if fa._is_bot_sender(sender):
            return reason, False
        in_thread = bool(getattr(message, "thread_id", None) or getattr(message, "root_id", None))
        mode = self._jev_admission_mode(in_thread=in_thread)
        if mode == "normal" or self._mentions_self(message):
            return reason, False
        chat_id = getattr(message, "chat_id", "") or ""
        if reason is not None and not self._allow_group_message(getattr(sender, "sender_id", None), chat_id, is_bot=False):
            return reason, False  # the group policy rejected the sender itself
        return (None, True) if mode == "judge" else ("jev_gate_unavailable", False)

    # --- Inbound routing (called from _process_inbound_message after build_source) ---

    async def _jev_admits(self, *, source, message, text, inbound_type, chat_id, chat_info, sender_profile,
                          message_id) -> bool:
        if inbound_type == MessageType.COMMAND:
            # Another bot's "/reset" must never reach this agent's command dispatch.
            fa_logger().debug("[Feishu] dropping unmentioned group command id=%s", message_id)
            return False
        actual_thread_id = getattr(message, "thread_id", None) or None
        root_message_id = getattr(message, "root_id", None) or None
        if actual_thread_id and self._thread_session_is_busy(source, root_message_id=root_message_id):
            # An unprompted reply never barges into a turn already running in this topic.
            fa_logger().info("[Feishu] dropping inbound event: thread_session_busy id=%s chat_id=%s",
                             message_id, chat_id)
            return False
        if not await self._jev_message_in_scope(text=text, chat_name=chat_info.get("name") or chat_id,
                                                sender_name=sender_profile["user_name"]):
            fa_logger().info("[Feishu] dropping inbound event: group_out_of_scope id=%s chat_id=%s", message_id, chat_id)
            return False
        return True

    def _key_auto_topic(self, *, source, message, message_id, chat_type, force_reply_thread: bool) -> None:
        """Key a turn on its prospective topic root, or reuse an automatic topic's root-keyed session."""
        actual_thread_id = getattr(message, "thread_id", None) or None
        root_message_id = getattr(message, "root_id", None) or None
        if ((self._reply_thread_enabled() or force_reply_thread) and chat_type in {"dm", "group"}
                and not actual_thread_id and message_id):
            # Feishu creates the omt_* topic only after the first reply: key the turn on its om_* id
            # now (also for a main-timeline quote reply, which Feishu roots at the reply itself).
            source.thread_id = str(message_id)
            self._mark_auto_thread_pending(source.thread_id)
        elif actual_thread_id and root_message_id:
            root_keyed = self._auto_thread_root_session(source, root_message_id)
            if root_keyed is not None:
                source.thread_id = root_keyed
                self._mark_auto_thread_established(root_keyed)

    async def _route_to_delegate(self, *, source, text, chat_id, chat_type, user_id) -> bool:
        """An active delegate loop or remote interaction owns this follow-up; gateway commands skip it."""
        command_text = str(text or "").strip()
        if is_gateway_command_during_delegate_loop(command_text):
            return False
        routed_text = command_text if command_text in _DELEGATE_LOOP_CONTROLS else text
        route = {"chat_id": chat_id, "thread_id": getattr(source, "thread_id", None), "user_id": user_id,
                 "chat_type": chat_type}
        if await self._maybe_route_delegate_interaction_message(text=routed_text, **route):
            return True
        return await self._maybe_route_delegate_foreground_message(text=routed_text, **route)

    async def _aegis_inbound(self, *, source, message, text, inbound_type, chat_id, chat_type, chat_info,
                             sender_profile, message_id, jev_gate_pending) -> Optional[str]:
        """Fork routing between ``build_source`` and dispatch: the event's ``channel_context``, or None when consumed.

        The recent group history rides ``channel_context`` (upstream's history-backfill field), never
        metadata: native admission accepts no adapter metadata outside WhatsApp, so a metadata block
        refused the whole turn. ``""`` means no history.

        Order matters: the Jev verdict decides whether the turn exists, the topic keying fixes the
        session identity the delegate routing and the history gate both depend on.
        """
        if jev_gate_pending and not await self._jev_admits(
                source=source, message=message, text=text, inbound_type=inbound_type, chat_id=chat_id,
                chat_info=chat_info, sender_profile=sender_profile, message_id=message_id):
            return None
        # A few embedders and test adapters return a partial source: route on the resolved identities.
        source_chat_type = getattr(source, "chat_type", None) or self._resolve_source_chat_type(
            chat_info=chat_info, event_chat_type=chat_type)
        self._key_auto_topic(source=source, message=message, message_id=message_id, chat_type=source_chat_type,
                             force_reply_thread=jev_gate_pending)
        if await self._route_to_delegate(source=source, text=text, chat_id=chat_id, chat_type=source_chat_type,
                                         user_id=getattr(source, "user_id", None) or sender_profile["user_id"]):
            return None
        if not self._should_inject_group_history(chat_type=chat_type, inbound_type=inbound_type, source=source):
            return ""
        return await self._fetch_group_history_block(
            chat_id=chat_id, exclude_message_id=message_id, thread_id=getattr(message, "thread_id", None) or None)

    # --- Card output around send() ---

    async def _card_send_first(self, *, chat_id, content, reply_to, metadata):
        """``(content, owed_files, card_result)``: files leave the text first, then the card takes it.

        A file is never card content: anything the gateway's own extraction did not take out
        (mid-turn commentary, delegate output) leaves as its own message. ``card_result`` is None
        when the send is not card-eligible (no turn, cards off, or a CardKit failure).
        """
        content, owed = self._split_card_mode_attachments(chat_id=chat_id, content=content, metadata=metadata)
        card_result = await self._send_via_card(chat_id=chat_id, content=content, reply_to=reply_to, metadata=metadata)
        if card_result is not None and owed:
            # Text first, then the files: the card names them, the bubbles below carry them.
            await self._dispatch_card_attachments(chat_id=chat_id, attachments=owed, metadata=metadata)
        return content, owed, card_result

    async def _with_owed_attachments(self, chat_id, owed, metadata, send):
        """Await the text/post send, then deliver files a declined card send already took out of it."""
        result = await send
        if owed:
            await self._dispatch_card_attachments(chat_id=chat_id, attachments=owed, metadata=metadata)
        return result

    async def _delete_via_card(self, message_id) -> Optional[bool]:
        """Retract a block inside a card this adapter owns; None when ``message_id`` is no card block."""
        manager = getattr(self, "_card_manager", None)
        if manager is None or not manager.owns_message(message_id):
            return None
        try:
            return bool(await manager.remove(message_id=message_id))
        except Exception:
            fa_logger().warning("[Feishu] card block retraction failed", exc_info=True)
            return False

    # --- Sends into a pending automatic topic (called from _feishu_send_with_retry) ---

    def _auto_thread_route(self, reply_to: Optional[str], metadata: Optional[dict]) -> tuple[Optional[str], Optional[dict], str]:
        """``(reply_to, metadata, pending_root)``: drop card block handles, flatten failed roots."""
        if self._is_card_block_handle(reply_to):
            # Streaming chains chunks by the id the previous send returned, a block handle in card
            # mode; thread metadata still places the message.
            fa_logger().debug("[Feishu] ignoring card block handle %s as a reply target", reply_to)
            reply_to = None
        root = str((metadata or {}).get("thread_id") or "")
        if root and root in self._auto_thread_state("_failed_auto_thread_roots"):
            return None, None, ""
        pending = self._is_message_thread_anchor(root) and root in self._auto_thread_state("_pending_auto_thread_roots")
        return reply_to, metadata, root if pending else ""

    async def _send_flat(self, *, chat_id, msg_type, payload) -> Any:
        return await self._send_raw_message(chat_id=chat_id, msg_type=msg_type, payload=payload, reply_to=None,
                                            metadata=None)

    async def _send_in_pending_topic(self, *, chat_id, msg_type, payload, reply_to, metadata, root) -> Any:
        """Send the topic-creating reply; on failure retry in the topic once or fall back flat once."""
        fa = _fa()
        retry_in_topic, last_error = False, None
        for attempt in range(fa._FEISHU_SEND_ATTEMPTS):
            try:
                response = await self._send_raw_message(chat_id=chat_id, msg_type=msg_type, payload=payload,
                                                        reply_to=reply_to, metadata=metadata)
            except Exception as exc:
                last_error = exc
                if msg_type == "post" and fa._POST_CONTENT_INVALID_RE.search(str(exc)):
                    raise
                if attempt < fa._FEISHU_SEND_ATTEMPTS - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                # The caller retries the whole message after an exception; only "flat" ends here.
                action = await self._auto_thread_failure_action(
                    root=root, chat_id=chat_id, msg_type=msg_type, payload=payload, reply_to=reply_to,
                    diag=f"raised after retries: {exc}", code=None)
                if action == "flat":
                    return await self._send_flat(chat_id=chat_id, msg_type=msg_type, payload=payload)
                raise
            if self._response_succeeded(response):
                self._mark_auto_thread_established(root)
                return response
            if msg_type == "post" and fa._POST_CONTENT_INVALID_RE.search(str(getattr(response, "msg", "") or "")):
                return response  # send() retries as plain text; formatting is no topic failure
            action = await self._auto_thread_failure_action(
                root=root, chat_id=chat_id, msg_type=msg_type, payload=payload, reply_to=reply_to,
                diag=self._feishu_response_diag(response), code=getattr(response, "code", None))
            if action == "give_up":
                return response
            if action != "retry":
                return await self._send_flat(chat_id=chat_id, msg_type=msg_type, payload=payload)
            retry_in_topic = True
            break
        if not retry_in_topic:
            raise last_error or RuntimeError("Feishu send failed")
        # Retried here, not by the caller: send() does not stop at a failed chunk. Bounded: the retry
        # lands, finds the topic established, or takes the second strike and goes flat.
        await asyncio.sleep(1)
        return await self._feishu_send_with_retry(chat_id=chat_id, msg_type=msg_type, payload=payload,
                                                  reply_to=reply_to, metadata=metadata)


def fa_logger():
    return _fa().logger
