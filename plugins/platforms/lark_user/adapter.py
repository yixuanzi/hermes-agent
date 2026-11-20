"""Lark personal-account auto-reply adapter (``lark_user``).

Polls the user's direct messages and group @mentions through ``lark-cli`` as the USER identity,
turns each into a gateway turn, and answers under the user's own name — after the user releases
the draft (``reply.delivery: review``, the default) or directly (``auto``). Configuration: README.md
in this directory.

Only a turn's final answer ever leaves: every final goes through ``send_final_ledgered``, which
marks its task; while a turn runs in a chat, any other send to that chat (tool progress, interim
commentary, heartbeats, error notices) is dropped, and ``_interim_send`` sends are dropped always.
"""

from __future__ import annotations

import asyncio
import collections
import contextvars
import datetime as dt
import hashlib
import logging
import time
from typing import Any, Optional

from gateway.config import Platform
from gateway.platforms.access_policy_mixin import OwnAccessPolicyMixin
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.platforms.event import MessageEvent, MessageType
from gateway.platforms.helpers import cancel_task, hash_for_log

from . import gateway_env
from .adapter_hint import PLATFORM_HINT
from .lark_user_cli import LarkCli, LarkCliError
from .lark_user_config import LarkUserSettings, SettingsError, load_settings
from .lark_user_context import group_context
from .lark_user_cursor import CursorStore, state_path
from .lark_user_intake import ChatRateLimit, Identity, drop_reason
from .lark_user_normalize import Inbound, to_inbound
from .lark_user_poll import PollSchedule
from .lark_user_render import render
from .lark_user_review import ReviewDesk, parse_command
from .lark_user_session import shape_source

logger = logging.getLogger(__name__)

_ORIGIN_CACHE = 500
_RETRYABLE = frozenset({"timeout", "rate_limit"})
_DROPPED = "lark_user:dropped"  # message id handed back for a suppressed non-final send
# Set while the gateway delivers a turn's final answer (task-local, so a concurrent progress send
# from another task is not mistaken for it).
_FINAL_SEND: contextvars.ContextVar[bool] = contextvars.ContextVar("lark_user_final_send", default=False)


def _iso(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


class LarkUserAdapter(OwnAccessPolicyMixin, BasePlatformAdapter):
    ALLOW_ALL_ENV_PREFIX = "LARK_USER"
    _TEXT_BATCH_DEFAULT_DELAY_S = 2.0  # a sender's burst of short messages becomes one turn

    def __init__(self, config, **kwargs):
        super().__init__(config=config, platform=Platform("lark_user"))
        try:
            self.settings, self._settings_error = load_settings(config.extra or {}), ""
        except SettingsError as exc:
            self.settings, self._settings_error = LarkUserSettings(), str(exc)
        s = self.settings
        # OwnAccessPolicyMixin contract.
        self._dm_policy, self._group_policy = s.dm_policy, s.group_policy
        self._allow_from, self._group_allow_from = s.allow_from, s.group_allow_from
        self._configure_text_batch_delays()
        self._cli = LarkCli(s.cli_bin, profile=s.cli_profile, timeout=s.cli_timeout)
        self._me: Optional[Identity] = None
        self._store: Optional[CursorStore] = None
        self._desk: Optional[ReviewDesk] = None
        self._tasks: list[asyncio.Task] = []
        self._rate = ChatRateLimit(s.rate_limit_per_chat)
        self._origins: collections.OrderedDict[str, Inbound] = collections.OrderedDict()
        self._last_by_chat: dict[str, str] = {}
        self._turns: collections.Counter[str] = collections.Counter()  # chat_id -> turns in flight

    @property
    def name(self) -> str:
        return "Lark (personal)"

    def _fail(self, code: str, message: str, *, retryable: bool) -> bool:
        logger.error("lark_user: %s", message)
        self._set_fatal_error(code, message, retryable=retryable)
        return False

    # ── lifecycle ───────────────────────────────────────────────────────────

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        if not await self._open():
            return False
        self._mark_connected()
        self._tasks = [asyncio.create_task(self._source_loop(name)) for name in self._sources()]
        logger.info("lark_user: polling %s for %s", ",".join(self._sources()), hash_for_log(self._me.open_id))
        return True

    async def _open(self) -> bool:
        """Resolve the Lark identity, take the account lock, load durable state; no polling yet."""
        if self._settings_error:
            return self._fail("config_invalid", f"platforms.lark_user: {self._settings_error}", retryable=False)
        if not self._cli.resolve_binary():
            return self._fail("cli_missing", f"{self.settings.cli_bin} not found; install lark-cli", retryable=False)
        try:
            info = await self._cli.user_info()
        except LarkCliError as exc:
            if exc.kind in {"auth", "config", "missing"}:
                return self._fail("auth_required", f"lark-cli user identity unavailable ({exc}); run "
                                  "`lark-cli auth login`", retryable=False)
            return self._fail("cli_error", f"lark-cli user_info failed: {exc}", retryable=True)
        me = Identity(str(info.get("open_id") or ""), str(info.get("name") or ""), str(info.get("tenant_key") or ""))
        if not me.open_id:
            return self._fail("auth_required", "lark-cli returned no user open_id; run `lark-cli auth login`",
                              retryable=False)
        if not self._acquire_platform_lock("lark_user", me.open_id, f"Lark account {hash_for_log(me.open_id)}"):
            return False
        self._me = me
        s = self.settings
        self._store = CursorStore(state_path(me.open_id), seen_ttl=max(86400.0, 4 * (s.poll.lookback + s.max_message_age)))
        self._store.load()
        self._desk = ReviewDesk(cli=self._cli, store=self._store, my_open_id=me.open_id, review_chat=s.review_chat,
                                ttl=s.review_ttl, deliver=self._deliver_for_review)
        return True

    async def disconnect(self) -> None:
        self._mark_disconnected()
        for task in self._tasks:
            await cancel_task(task)
        self._tasks = []
        if self._store is not None:
            self._store.save()
        self._release_platform_lock()

    def _sources(self) -> list[str]:
        poll = self.settings.poll
        # The dm search also carries the user's own review commands, so review keeps it running.
        sources = ["dm"] if poll.dm.enabled or self.settings.uses_review else []
        return sources + (["mention"] if poll.mention.enabled else [])

    # ── polling ─────────────────────────────────────────────────────────────

    async def _source_loop(self, source: str) -> None:
        poll = self.settings.poll
        cfg = poll.dm if source == "dm" else poll.mention
        schedule = PollSchedule(interval=cfg.interval, idle_interval=poll.idle_interval, idle_after=poll.idle_after,
                                jitter=poll.jitter, max_backoff=poll.max_backoff, active_hours=poll.active_hours)
        while self._running:
            wait = schedule.wait_for_window(dt.datetime.now())
            if wait:
                await asyncio.sleep(wait)
                continue
            try:
                schedule.on_result(await self.poll_once(source))
            except LarkCliError as exc:
                if exc.kind == "auth":
                    await self._park_for_auth(exc)
                    return
                schedule.on_error(exc.kind)
                logger.warning("lark_user: %s poll failed (%s); retrying in backoff", source, exc.kind)
            except Exception:
                schedule.on_error("api")
                logger.exception("lark_user: %s poll crashed", source)
            await asyncio.sleep(schedule.next_delay())

    async def _park_for_auth(self, exc: LarkCliError) -> None:
        # The expired token cannot send a notice either: surface it through runtime status only.
        self._set_fatal_error("auth_required", f"lark-cli user token rejected ({exc}); run `lark-cli auth login`",
                              retryable=False)
        await self._notify_fatal_error()

    async def poll_once(self, source: str) -> int:
        """One search for ``source``; returns how many messages became turns."""
        store, me = self._store, self._me
        now = time.time()
        primed = source in store.watermarks
        start = min(store.watermarks.get(source, now), now) - self.settings.poll.lookback
        items = await self._cli.search(chat_type="p2p" if source == "dm" else "group", start=_iso(start),
                                       at_me=source == "mention")
        inbound = sorted(filter(None, (to_inbound(i, my_open_id=me.open_id, my_name=me.name) for i in items)),
                         key=lambda m: m.created_at)
        dispatched = 0
        for inb in inbound:
            if store.is_seen(inb.message_id):
                continue
            if not primed:  # first run for this account: what is already there is history, not work
                self._commit(source, inb)
                continue
            if self._is_review_command(source, inb):
                self._commit(source, inb)  # at most once: a command has side effects
                store.save()
                await self._desk.handle(inb.text)
                continue
            reason = drop_reason(inb, source, me=me, settings=self.settings, now=now,
                                 dm_allowed=self._is_dm_allowed, group_allowed=self._is_group_allowed)
            if reason is None and not self._rate.allow(inb.chat_id):
                reason = "rate_limited"
            if reason:
                logger.debug("lark_user: skip %s (%s)", inb.message_id, reason)
            else:
                await self._dispatch(inb)
                dispatched += 1
            self._commit(source, inb)
        store.watermarks.setdefault(source, now)
        if source == "dm" and self._desk is not None:
            await self._desk.expire(now)
        store.save()
        return dispatched

    def _commit(self, source: str, inb: Inbound) -> None:
        self._store.mark_seen(inb.message_id)
        self._store.advance(source, inb.created_at)

    def _is_review_command(self, source: str, inb: Inbound) -> bool:
        desk = self._desk
        return (source == "dm" and desk is not None and bool(desk.chat_id) and inb.chat_id == desk.chat_id
                and inb.sender_id == self._me.open_id and parse_command(inb.text) is not None)

    async def _dispatch(self, inb: Inbound) -> None:
        self._origins[inb.message_id] = inb
        while len(self._origins) > _ORIGIN_CACHE:
            self._origins.popitem(last=False)
        self._last_by_chat[inb.chat_id] = inb.message_id
        chat = self.settings.for_chat(inb.chat_id)
        source = shape_source(self.build_source, inb, chat)
        event = MessageEvent(
            text=inb.text, message_type=MessageType.TEXT, source=source, message_id=inb.message_id,
            user_id=inb.sender_id, user_name=inb.sender_name or None,
            timestamp=dt.datetime.fromtimestamp(inb.created_at or time.time()),
            # Senders are third parties writing to the user, never operators of this gateway.
            allow_gateway_control=False,
        )
        if inb.chat_type == "group" and chat.context.enabled and self._wants_context(event, chat.context.when):
            event.channel_context = await group_context(self._cli, inb, chat.context) or None
        logger.info("lark_user: turn for %s chat=%s (%d chars)", inb.message_id, hash_for_log(inb.chat_id),
                    len(inb.text))
        if self._text_batch_delay_seconds > 0:
            self._enqueue_text_event(event)
        else:
            await self.handle_message(event)

    def _wants_context(self, event: MessageEvent, when: str) -> bool:
        """``new_session``: only when the event's session has no transcript yet."""
        if when == "always":
            return True
        peek = getattr(getattr(self, "_session_store", None), "peek_session_id", None)
        return not (callable(peek) and peek(self._event_session_key(event)))

    # ── turn boundaries: only the final answer leaves ───────────────────────

    async def on_processing_start(self, event: MessageEvent) -> None:
        self._turns[event.source.chat_id] += 1
        await super().on_processing_start(event)

    async def on_processing_complete(self, event: MessageEvent, outcome) -> None:
        chat_id = event.source.chat_id
        self._turns[chat_id] -= 1
        if self._turns[chat_id] <= 0:
            del self._turns[chat_id]
        await super().on_processing_complete(event, outcome)

    async def send_final_ledgered(self, *args, **kwargs):
        token = _FINAL_SEND.set(True)
        try:
            return await super().send_final_ledgered(*args, **kwargs)
        finally:
            _FINAL_SEND.reset(token)

    def _is_final_only_drop(self, chat_id: str, metadata: Optional[dict[str, Any]]) -> bool:
        if (metadata or {}).get("_interim_send"):
            return True
        return chat_id in self._turns and not _FINAL_SEND.get()

    async def edit_message(self, chat_id: str, message_id: str, content: str, *, finalize: bool = False) -> SendResult:
        # A user identity cannot edit text; a suppressed progress bubble "edits" into nothing.
        if message_id == _DROPPED:
            return SendResult(success=True, message_id=_DROPPED)
        return SendResult(success=False, error="Not supported")

    # ── outbound ────────────────────────────────────────────────────────────

    def _draft_for(self, chat_id: str, reply_to: Optional[str]) -> dict[str, Any]:
        anchor = reply_to or self._last_by_chat.get(chat_id, "")
        origin = self._origins.get(anchor)
        return {
            "chat_id": chat_id, "reply_to": anchor,
            "origin": {
                "sender_id": origin.sender_id, "sender_name": origin.sender_name, "chat_type": origin.chat_type,
                "chat_name": origin.chat_name if origin.chat_type == "group" else "", "text": origin.text,
            } if origin else {},
        }

    async def _deliver(self, draft: dict[str, Any], content: str) -> str:
        """Render and send ``content`` for ``draft``; returns the last message id."""
        reply = self.settings.for_chat(draft["chat_id"]).reply
        origin = draft.get("origin") or {}
        variables = {"user_name": self._me.name, "sender_name": origin.get("sender_name") or "",
                     "time": dt.datetime.now().strftime("%H:%M"), "date": dt.date.today().isoformat()}
        mention = ((origin["sender_id"], origin.get("sender_name") or "")
                   if reply.at_sender and origin.get("chat_type") == "group" and origin.get("sender_id") else None)
        anchor = draft.get("reply_to") or ""
        last = ""
        for index, body in enumerate(render(content, reply, variables, mention=mention)):
            key = hashlib.sha1(f"{anchor}|{draft['chat_id']}|{index}|{body}".encode()).hexdigest()[:40]
            if anchor and reply.target != "send":
                sent = await self._cli.reply(anchor, body, fmt=reply.format, in_thread=reply.target == "thread",
                                             idempotency_key=key)
            else:
                sent = await self._cli.send(body, fmt=reply.format, chat_id=draft["chat_id"], idempotency_key=key)
            last = sent.message_id
        return last

    async def _deliver_for_review(self, draft: dict[str, Any], content: str) -> str:
        try:
            await self._deliver(draft, content)
        except LarkCliError as exc:
            return str(exc)
        return ""

    async def send(self, chat_id: str, content: str, reply_to: Optional[str] = None,
                   metadata: Optional[dict[str, Any]] = None) -> SendResult:
        if self._me is None or self._desk is None:
            return SendResult(success=False, error="Not connected")
        if self._is_final_only_drop(chat_id, metadata):
            logger.debug("lark_user: dropped a non-final send to %s (%d chars)", hash_for_log(chat_id), len(content))
            return SendResult(success=True, message_id=_DROPPED)
        draft = self._draft_for(chat_id, reply_to)
        try:
            if self.settings.for_chat(chat_id).reply.delivery == "review" and chat_id != self._desk.chat_id:
                message_id = await self._desk.submit(draft, content)
            else:
                message_id = await self._deliver(draft, content)
        except LarkCliError as exc:
            return SendResult(success=False, error=str(exc), retryable=exc.kind in _RETRYABLE)
        return SendResult(success=True, message_id=message_id)

    async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        origin = self._origins.get(self._last_by_chat.get(chat_id, ""))
        is_group = bool(origin and origin.chat_type == "group")
        return {"name": (origin.chat_name if is_group else origin.sender_name) if origin else chat_id,
                "type": "group" if is_group else "dm", "chat_id": chat_id}


# ── registration ─────────────────────────────────────────────────────────────

def check_requirements() -> bool:
    """Passive probe: ``lark-cli`` is on PATH (login state is checked at connect)."""
    return LarkCli().resolve_binary() is not None


def _enabled(config) -> bool:
    return bool(getattr(config, "enabled", False))


def register(ctx):
    ctx.register_platform(
        name="lark_user",
        label="Lark (personal)",
        dashboard_description="Auto-reply to your own Lark direct messages and @mentions, as you, via lark-cli.",
        docs_url="https://hermes-agent.nousresearch.com/docs/user-guide/messaging/lark-user",
        adapter_factory=LarkUserAdapter,
        check_fn=check_requirements,
        is_connected=_enabled,
        config_connected_fn=_enabled,
        install_hint="Install lark-cli and run `lark-cli auth login` (user identity)",
        max_message_length=0,  # the adapter chunks itself so the prefix/suffix land once
        emoji="🪶",
        allow_update_command=False,
        display_tier="minimal",
        platform_hint=PLATFORM_HINT,
        **gateway_env.AUTHZ,
    )
