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
import uuid
from typing import Any, Optional

from gateway.config import Platform
from gateway.platforms.access_policy_mixin import OwnAccessPolicyMixin
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.platforms._shared import get_scoped_secret as _get_secret
from gateway.platforms.event import MessageEvent, MessageType
from gateway.platforms.helpers import cancel_task, hash_for_log
from hermes_constants import display_hermes_home

from . import gateway_env
from .adapter_hint import PLATFORM_HINT
from .lark_user_cli import LarkCli, LarkCliError
from .lark_user_config import ChatSettings, LarkUserSettings, SettingsError, load_settings
from .lark_user_context import group_context
from .lark_user_control import conversation_list, lark_user_command, register_live, unregister_live
from .lark_user_cursor import CursorStore
from .lark_user_intake import ChatRateLimit, Identity, drop_reason
from .lark_user_media import fetch_attachments
from .lark_user_normalize import Inbound, to_inbound
from . import lark_user_overrides as overrides
from .lark_user_poll import PollSchedule
from .lark_user_render import render
from .lark_user_review import CardPresenter, ReviewDesk, TextPresenter, parse_command
from .lark_user_review_bot import APP_ID_ENV, APP_SECRET_ENV, ReviewBot
from .lark_user_session import ThreadRoots, expiry_reason, shape_source

logger = logging.getLogger(__name__)

_ORIGIN_CACHE = 500
_RETRYABLE = frozenset({"timeout", "rate_limit"})
_DROPPED = "lark_user:dropped"  # message id handed back for a suppressed non-final send
_CONTROL_ID = "lark_user-control-"  # message ids of the gateway commands the plugin runs itself
# Set while the gateway delivers a turn's final answer (task-local, so a concurrent progress send
# from another task is not mistaken for it).
_FINAL_SEND: contextvars.ContextVar[bool] = contextvars.ContextVar("lark_user_final_send", default=False)
_REVISION_PROMPT = (
    "[审核修订意见]\n这不是对方发来的新消息。{user_name} 审阅了你刚才拟好的回复草稿，要求修改：\n{notes}\n\n"
    "上一版草稿：\n<<<\n{content}\n>>>\n\n请按修改意见重写给对方的回复，只输出修改后的完整回复正文。"
)


def _login_hint() -> str:
    # lark-cli keeps a separate "hermes" workspace (~/.lark-cli/hermes/) that it selects whenever
    # HERMES_HOME is set: a login from a plain terminal lands in the other workspace.
    return f"run `HERMES_HOME={display_hermes_home()} lark-cli auth login`"


def _iso(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


class LarkUserAdapter(OwnAccessPolicyMixin, BasePlatformAdapter):
    ALLOW_ALL_ENV_PREFIX = "LARK_USER"
    _TEXT_BATCH_DEFAULT_DELAY_S = 2.0  # a sender's burst of short messages becomes one turn

    def __init__(self, config, **kwargs):
        super().__init__(config=config, platform=Platform("lark_user"))
        self._config_extra: dict[str, Any] = dict(config.extra or {})  # as config.yaml has it; overrides go on top
        self._review_creds: tuple[str, str] = ("", "")
        self._cli_config: dict[str, Any] = {}
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
        self._review_bot: Optional[ReviewBot] = None
        # anchor message id -> revision being produced for it (count, notes, origin)
        self._revisions: dict[str, dict[str, Any]] = {}
        self._tasks: list[asyncio.Task] = []  # review-card listener: runs whenever connected
        # Auto-reply switch (/lark-user): in memory, OFF on every connect. Polling runs only while on.
        self._active = False
        self._active_since = 0.0
        self._poll_tasks: list[asyncio.Task] = []
        self._reprime: set[str] = set()  # sources whose next poll treats what is there as history
        self._inflight: dict[str, Any] = {}  # session key -> source of a turn in flight
        self._union_id = ""
        self._rate = ChatRateLimit(s.rate_limit_per_chat)
        self._origins: collections.OrderedDict[str, Inbound] = collections.OrderedDict()
        self._last_by_chat: dict[str, str] = {}
        self._turns: collections.Counter[str] = collections.Counter()  # chat_id -> turns in flight
        self._thread_roots = ThreadRoots(self._cli.thread_root)

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
        if self._review_bot is not None:  # pending drafts stay decidable while auto-reply is off
            self._tasks.append(asyncio.create_task(self._review_bot.consume(self._on_review_click)))
        self._active = False
        register_live(self)
        logger.info("lark_user: connected for %s; auto-reply is OFF until /lark-user start",
                    hash_for_log(self._me.open_id))
        return True

    async def _open(self) -> bool:
        """Resolve the Lark identity, take the account lock, load durable state; no polling yet."""
        if self._settings_error:
            return self._fail("config_invalid", f"platforms.lark_user: {self._settings_error}", retryable=False)
        self._apply_overrides()
        if not self._cli.resolve_binary():
            return self._fail("cli_missing", f"{self.settings.cli_bin} not found; install lark-cli", retryable=False)
        try:
            info = await self._cli.user_info()
        except LarkCliError as exc:
            if exc.kind in {"auth", "config", "missing"}:
                return self._fail("auth_required", f"lark-cli user identity unavailable ({exc}); {_login_hint()}",
                                  retryable=False)
            return self._fail("cli_error", f"lark-cli user_info failed: {exc}", retryable=True)
        me = Identity(str(info.get("open_id") or ""), str(info.get("name") or ""), str(info.get("tenant_key") or ""))
        if not me.open_id:
            return self._fail("auth_required", f"lark-cli returned no user open_id; {_login_hint()}",
                              retryable=False)
        if not self._acquire_platform_lock("lark_user", me.open_id, f"Lark account {hash_for_log(me.open_id)}"):
            return False
        self._me = me
        # Which lark-cli config this process actually uses: HERMES_HOME makes lark-cli pick its
        # "hermes" workspace, so a gateway and a plain terminal can be logged into different apps.
        try:
            cli_config = await self._cli.call(["config", "show"])
        except LarkCliError as exc:
            cli_config = {}
            logger.warning("lark_user: lark-cli config unreadable (%s)", exc.kind)
        logger.info("lark_user: lark-cli workspace=%s profile=%s app=%s user=%s", cli_config.get("workspace") or "?",
                    cli_config.get("profile") or "?", cli_config.get("appId") or "?", hash_for_log(me.open_id))
        s = self.settings
        if self._store is not None:  # reconnect
            self._store.close()
        self._store = CursorStore.open(me.open_id, seen_ttl=max(86400.0, 4 * (s.poll.lookback + s.max_message_age)))
        self._union_id = str(info.get("union_id") or "")
        self._cli_config = cli_config
        # Read once, in the profile scope connect runs in: a later /lark-user reply.delivery review may
        # need them from a command handler.
        self._review_creds = (_get_secret(APP_ID_ENV, ""), _get_secret(APP_SECRET_ENV, ""))
        failure = await self._open_review()
        return self._fail(*failure[:2], retryable=failure[2]) if failure else True

    def _review_mode(self) -> str:
        s = self.settings
        return s.review_interactive or ("card" if all(self._review_creds) and s.uses_review else "text")

    async def _open_review(self) -> Optional[tuple[str, str, bool]]:
        """Pick the review presentation: cards from the review app when it is configured (or
        ``review.interactive: card`` demands it), else text commands in the user's own chat. Returns
        (code, message, retryable) when the cards cannot be had; the review desk is then left as it was."""
        s = self.settings
        (app_id, secret), cli_config, union_id = self._review_creds, self._cli_config, self._union_id
        presenter: Any = TextPresenter(self._cli, self._store, my_open_id=self._me.open_id, review_chat=s.review_chat)
        if self._review_mode() == "card":
            if not (app_id and secret):
                return ("config_invalid", f"review.interactive: card needs {APP_ID_ENV} and {APP_SECRET_ENV} "
                        "in .env (a Lark app used only for review cards)", False)
            try:
                if app_id in {_get_secret("FEISHU_APP_ID", ""), str(cli_config.get("appId") or "")}:
                    return ("config_invalid", f"{APP_ID_ENV} is an app another connection already uses "
                            "(the Feishu platform or lark-cli's user login); card clicks would be split "
                            "between them. Use an app reserved for review cards.", False)
                bot = ReviewBot(cli=LarkCli(s.cli_bin, timeout=s.cli_timeout), app_id=app_id, app_secret=secret,
                                brand=str(cli_config.get("brand") or "feishu"))
                await bot.check()
            except LarkCliError as exc:
                return "review_app_error", f"review app unavailable: {exc}", exc.kind != "auth"
            if not union_id:
                return ("review_app_error", "lark-cli user_info returned no union_id to reach the user "
                        "from the review app", False)
            self._review_bot, presenter = bot, CardPresenter(bot, union_id)
        self._desk = ReviewDesk(store=self._store, presenter=presenter, ttl=s.review_ttl,
                                deliver=self._deliver_for_review, revise=self._start_revision,
                                revising_allowed=lambda: self._active)
        return None

    async def disconnect(self) -> None:
        self._mark_disconnected()
        unregister_live(self)
        await self._stop_polling()
        for task in self._tasks:
            await cancel_task(task)
        self._tasks = []
        if self._review_bot is not None:
            await self._review_bot.close()
        if self._store is not None:
            self._store.close()
            self._store = None
        self._release_platform_lock()

    # ── auto-reply switch (/lark-user) ─────────────────────────────────────

    def serves_profile(self, profile: Optional[str]) -> bool:
        def norm(name: Optional[str]) -> str:
            return (name or "").strip() or "default"
        return self._me is not None and norm(profile) == norm(getattr(self, "_owner_profile", None))

    def is_owner(self, source: Any) -> bool:
        """The user this plugin speaks for: a local session on this machine, or a Feishu/Lark sender
        carrying that user's union_id (any app) or open_id (lark-cli's app)."""
        if source is None or self._me is None:
            return False
        if getattr(source, "platform", None) == Platform.LOCAL:
            return True
        return bool((self._union_id and getattr(source, "user_id_alt", None) == self._union_id)
                    or getattr(source, "user_id", None) == self._me.open_id)

    async def start_auto_reply(self) -> str:
        if self._active:
            return "lark_user 自动回复已经在运行。\n" + self.auto_reply_status()
        self._active, self._active_since = True, time.time()
        self._reprime = set(self._sources())  # what arrived while it was off is history
        self._poll_tasks = [self._poll_task(name) for name in self._sources()]
        logger.info("lark_user: auto-reply ON, polling %s", ",".join(self._sources()))
        return "✅ lark_user 自动回复已开启（关闭期间收到的消息不会补回复）。\n" + self.auto_reply_status()

    async def stop_auto_reply(self) -> str:
        if not self._active:
            return "lark_user 自动回复本来就是关闭的。\n" + self.auto_reply_status()
        await self._stop_polling()
        stopped = await self._halt_turns()
        logger.info("lark_user: auto-reply OFF (%d running turn(s) interrupted)", stopped)
        return (f"⏹️ lark_user 自动回复已关闭：停止拉取消息，中断了 {stopped} 个正在执行的任务，"
                "关闭期间不执行任务、不产生任何回复或草稿。\n" + self.auto_reply_status())

    async def _stop_polling(self) -> None:
        self._active = False
        tasks, self._poll_tasks = self._poll_tasks, []
        for task in tasks:
            await cancel_task(task)

    async def _halt_turns(self) -> int:
        """Drop queued input and interrupt every turn in flight through the gateway's own /stop,
        sent as that conversation's source (the path that interrupts the agent and frees its lock)."""
        for task in list(self._pending_text_batch_tasks.values()):
            task.cancel()
        self._pending_text_batches.clear()
        self._pending_messages.clear()
        running = list(self._inflight.values())
        for source in running:
            await self._run_control("/stop", source)
        return len(running)

    async def _run_control(self, command: str, source: Any) -> None:
        """Run a gateway command as ``source``'s own conversation. Its answer is the plugin's, never
        the sender's: ``send`` drops every reply anchored to a control id."""
        await self.handle_message(MessageEvent(text=command, message_type=MessageType.COMMAND, source=source,
                                               message_id=f"{_CONTROL_ID}{uuid.uuid4().hex}"))

    def _live_conversations(self) -> dict[str, list[Any]]:
        """chat_id -> this profile's lark_user sessions that have not ended, whatever scope cut them."""
        store = getattr(self, "_session_store", None)
        chats: dict[str, list[Any]] = collections.defaultdict(list)
        for entry in store.list_sessions() if store is not None else ():
            origin = entry.origin
            if (origin is not None and origin.platform == self.platform and not entry.suspended
                    and self.serves_profile(getattr(origin, "profile", None))):
                chats[origin.chat_id].append(entry)
        return chats

    async def reset_conversations(self, chat_id: str) -> str:
        """End every live conversation of one chat (a DM or a group: per user, per topic or shared);
        a turn still running in one is interrupted. Without a chat id, list the chats that have some."""
        chats = self._live_conversations()
        if not chat_id:
            return conversation_list(chats)
        entries = chats.get(chat_id, [])
        if not entries:
            return f"{chat_id} 没有进行中的会话。会话 ID 区分大小写；发送 /lark-user reset 查看列表。"
        interrupted = 0
        for entry in entries:
            if (source := self._inflight.get(entry.session_key)) is not None:
                await self._run_control("/stop", source)
                interrupted += 1
            # Suspended: the gateway ends it and opens a fresh one on the chat's next message (as /new).
            await asyncio.to_thread(self._session_store.suspend_session, entry.session_key)
        logger.info("lark_user: reset %d conversation(s) of chat %s on request (%d running turn(s) interrupted)",
                    len(entries), hash_for_log(chat_id), interrupted)
        return (f"🔄 已重置 {chat_id} 的 {len(entries)} 个会话"
                + (f"，中断了 {interrupted} 个正在执行的任务" if interrupted else "")
                + "。这个会话的下一条消息会从新会话开始。")

    def auto_reply_status(self) -> str:
        review = type(self._desk.presenter).__name__.replace("Presenter", "").lower() if self._desk else "?"
        delivery = self.settings.defaults.reply.delivery
        lines = [f"状态：{'🟢 运行中' if self._active else '⚪ 已关闭'}"
                 + (f"（{time.strftime('%m-%d %H:%M', time.localtime(self._active_since))} 起）" if self._active else ""),
                 f"轮询来源：{', '.join(self._sources()) or '无'}",
                 f"默认回复方式：{'审核后发送' if delivery == 'review' else '直接发送'}（审核呈现：{review}）",
                 f"待审核草稿：{len(self._store.drafts) if self._store else 0} 条"]
        if changed := self._overrides():
            lines.append(f"运行时修改的回复设置：{len(changed)} 项（/lark-user reply 查看，重启后恢复配置文件）")
        if not self._active:
            lines.append("发送 /lark-user start 开启。")
        return "\n".join(lines)

    # ── in-memory reply settings (/lark-user reply.<key>) ──────────────────

    def _overrides(self) -> dict[overrides.Path, Any]:
        return overrides.for_profile(getattr(self, "_owner_profile", None))

    def _apply_overrides(self) -> None:
        """Lay this process's ``/lark-user reply…`` changes over config.yaml (a reconnect retry builds
        a new adapter: the changes outlive it, never the process)."""
        changed = self._overrides()
        if not changed:
            return
        try:
            self.settings = load_settings(overrides.layered(self._config_extra, changed))
        except SettingsError as exc:  # config.yaml changed under them: the file wins
            logger.warning("lark_user: in-memory reply settings dropped, they no longer fit the config (%s)", exc)
            changed.clear()

    async def reply_option(self, path: str, value: str) -> str:
        """``/lark-user reply[.<key>] [value | --reset]``: show, change or restore reply settings, in
        memory only. A value is validated by ``load_settings`` exactly as if config.yaml held it."""
        changed, value = self._overrides(), value.strip()
        configured = load_settings(self._config_extra)
        if path.lower() == "reply":
            if value == overrides.RESET:
                changed.clear()
                return await self._adopt(configured, "↩️ 已全部恢复为配置文件里的回复设置。")
            if value:
                return "❌ 未修改：要改哪一项？例如 /lark-user reply.format card；全部恢复：/lark-user reply --reset"
            return overrides.describe(self.settings, configured, changed)
        try:
            target = overrides.parse_path(path)
            if not value:
                return overrides.describe(self.settings, configured, changed, target)
            candidate = {k: v for k, v in changed.items() if k != target}
            if value != overrides.RESET:
                candidate[target] = overrides.parse_value(target[1], value)
            settings = load_settings(overrides.layered(self._config_extra, candidate))
        except (overrides.OverrideError, SettingsError) as exc:
            return f"❌ 未修改：{exc}"
        changed.clear()
        changed.update(candidate)
        logger.info("lark_user: %s %s in memory", overrides.label(target),
                    "restored" if value == overrides.RESET else "changed")
        verb = "已恢复" if value == overrides.RESET else "已修改（仅本进程有效，重启后恢复配置文件）"
        return await self._adopt(settings, f"✅ {verb}：{overrides.describe(settings, configured, changed, target)}")

    async def _adopt(self, settings: LarkUserSettings, message: str) -> str:
        """Switch to ``settings``: the next reply uses them. Turning review on where nothing reviewed
        before also brings up what review needs (the review app's cards, the dm poll that carries the
        user's text review commands)."""
        was_review, self.settings = self.settings.uses_review, settings
        if was_review or not settings.uses_review or self._me is None:
            return message
        if self._review_bot is None and self._review_mode() == "card":
            failure = await self._open_review()
            if failure:
                message += f"\n⚠️ 审核卡片不可用（{failure[1]}），草稿改用文本审核。"
                logger.warning("lark_user: review cards unavailable on switching to review (%s)", failure[0])
            else:
                self._tasks.append(asyncio.create_task(self._review_bot.consume(self._on_review_click)))
        if self._active:
            polled = {task.get_name() for task in self._poll_tasks if not task.done()}
            for source in self._sources():
                if self._poll_task_name(source) not in polled:
                    self._reprime.add(source)  # what is already there is history
                    self._poll_tasks.append(self._poll_task(source))
        return message

    @staticmethod
    def _poll_task_name(source: str) -> str:
        return f"lark_user-poll-{source}"

    def _poll_task(self, source: str) -> asyncio.Task:
        return asyncio.create_task(self._source_loop(source), name=self._poll_task_name(source))

    def _sources(self) -> list[str]:
        poll = self.settings.poll
        # The dm search also carries the user's own text review commands, so text review keeps it running.
        sources = ["dm"] if poll.dm.enabled or (self.settings.uses_review and self._text_review) else []
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
        self._set_fatal_error("auth_required", f"lark-cli user token rejected ({exc}); {_login_hint()}",
                              retryable=False)
        await self._notify_fatal_error()

    async def poll_once(self, source: str) -> int:
        """One search for ``source``; returns how many messages became turns."""
        store, me = self._store, self._me
        now = time.time()
        watermark = store.watermark(source)
        primed = watermark is not None and source not in self._reprime
        start = min(watermark if watermark is not None else now, now) - self.settings.poll.lookback
        items = await self._cli.search(chat_type="p2p" if source == "dm" else "group", start=_iso(start),
                                       at_me=source == "mention")
        inbound = sorted(filter(None, (to_inbound(i, my_open_id=me.open_id, my_name=me.name) for i in items)),
                         key=lambda m: m.created_at)
        ready: list[Inbound] = []
        for inb in inbound:
            if store.is_seen(inb.message_id):
                continue
            if not primed:  # first run for this account: what is already there is history, not work
                self._commit(source, inb)
                continue
            if self._is_review_command(source, inb):
                self._commit(source, inb)  # at most once: a command has side effects
                await self._desk.handle_text(inb.text)
                continue
            reason = drop_reason(inb, source, me=me, settings=self.settings, now=now,
                                 dm_allowed=self._is_dm_allowed, group_allowed=self._is_group_allowed)
            if reason is None and not self._rate.allow(inb.chat_id):
                reason = "rate_limited"
            if reason:
                logger.debug("lark_user: skip %s (%s)", inb.message_id, reason)
                self._commit(source, inb)
            else:
                ready.append(inb)
        # Every turn is prepared before any is handed off: preparing one (attachments, topic root,
        # group context) can take longer than the text batch delay, which would otherwise flush a
        # sender's earlier message alone and turn one burst found by this poll into several turns.
        events = [await self._prepare(inb) for inb in ready]
        for inb, event in zip(ready, events):
            await self._hand_off(event)
            self._commit(source, inb)
        if not primed:  # history taken in: answer only what arrives from now on
            store.advance(source, now)
            self._reprime.discard(source)
        if source == "dm" and self._desk is not None:
            await self._desk.expire(now)
        store.prune(now)
        return len(ready)

    def _commit(self, source: str, inb: Inbound) -> None:
        self._store.mark_seen(inb.message_id)
        self._store.advance(source, inb.created_at)

    @property
    def _text_review(self) -> bool:
        return self._desk is not None and isinstance(self._desk.presenter, TextPresenter)

    def _is_review_command(self, source: str, inb: Inbound) -> bool:
        desk = self._desk
        return (source == "dm" and self._text_review and bool(desk.chat_id) and inb.chat_id == desk.chat_id
                and inb.sender_id == self._me.open_id and parse_command(inb.text) is not None)

    async def _prepare(self, inb: Inbound) -> MessageEvent:
        """The turn ``inb`` starts, with everything it needs fetched; not handed off yet."""
        self._origins[inb.message_id] = inb
        while len(self._origins) > _ORIGIN_CACHE:
            self._origins.popitem(last=False)
        self._last_by_chat[inb.chat_id] = inb.message_id
        chat = self.settings.for_chat(inb.chat_id)
        source = shape_source(self.build_source, inb, chat, thread_root=await self._thread_roots.root_for(inb, chat))
        attachments = await fetch_attachments(self._cli, inb.message_id, inb.text)
        event = MessageEvent(
            text=inb.text, message_type=MessageType.TEXT, source=source, message_id=inb.message_id,
            user_id=inb.sender_id, user_name=inb.sender_name or None,
            timestamp=dt.datetime.fromtimestamp(inb.created_at or time.time()),
            media_urls=[a.path for a in attachments], media_types=[a.media_type for a in attachments],
            media_filenames=[a.name for a in attachments], media_text_inlined=[False] * len(attachments),
            channel_prompt=chat.instructions or None,
            # Senders are third parties writing to the user, never operators of this gateway.
            allow_gateway_control=False,
        )
        fresh = await self._open_conversation(event, chat)
        if inb.chat_type == "group" and chat.context.enabled and (fresh or chat.context.when == "always"):
            event.channel_context = await group_context(self._cli, inb, chat.context) or None
        logger.info("lark_user: turn for %s chat=%s (%d chars, %d attachment(s))", inb.message_id,
                    hash_for_log(inb.chat_id), len(inb.text), len(attachments))
        return event

    async def _hand_off(self, event: MessageEvent) -> None:
        if self._text_batch_delay_seconds > 0:
            self._enqueue_text_event(event)
        else:
            await self.handle_message(event)

    async def _open_conversation(self, event: MessageEvent, chat: ChatSettings) -> bool:
        """End the event's conversation if it went stale (``session.idle_reset`` / ``daily_reset_at``);
        True when the event starts a new one. Suspending is the gateway's own reset-on-next-access
        path (/stop uses it): the turn this event starts ends the old session and opens a fresh one,
        hooks and memory included, and the reset notice it emits mid-turn never leaves."""
        store = getattr(self, "_session_store", None)
        key = self._event_session_key(event)
        entry = store.lookup_by_session_key(key) if store is not None else None
        if entry is None or entry.suspended:
            return True
        if key in self._inflight:
            return False
        reason = expiry_reason(entry.updated_at.timestamp(), time.time(), chat)
        if not reason:
            return False
        await asyncio.to_thread(store.suspend_session, key)
        logger.info("lark_user: conversation %s ended (%s); %s starts a new one", hash_for_log(key), reason,
                    event.message_id)
        return True

    # ── turn boundaries: only the final answer leaves ───────────────────────

    async def on_processing_start(self, event: MessageEvent) -> None:
        self._turns[event.source.chat_id] += 1
        self._inflight[self._event_session_key(event)] = event.source
        await super().on_processing_start(event)

    async def on_processing_complete(self, event: MessageEvent, outcome) -> None:
        chat_id = event.source.chat_id
        self._inflight.pop(self._event_session_key(event), None)
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
        revision = self._revisions.pop(anchor, {})
        return {
            "chat_id": chat_id, "reply_to": anchor,
            # Everything a later revision needs to rebuild the conversation's source, even after a restart.
            "origin": {
                "message_id": origin.message_id, "sender_id": origin.sender_id, "sender_name": origin.sender_name,
                "chat_type": origin.chat_type, "chat_name": origin.chat_name if origin.chat_type == "group" else "",
                "thread_id": origin.thread_id, "tenant_key": origin.tenant_key, "text": origin.text,
            } if origin else revision.get("origin", {}),
            **({"revision": revision["revision"], "revision_note": revision["notes"]} if revision else {}),
        }

    async def _start_revision(self, draft: dict[str, Any], notes: str) -> None:
        """Send the user's revision notes back into the conversation's own session; the rewritten reply
        returns through ``send`` as a new draft that remembers which revision it is."""
        origin, anchor = draft.get("origin") or {}, draft.get("reply_to") or ""
        inb = self._origins.get(anchor) or Inbound(
            message_id=anchor, chat_id=draft["chat_id"], chat_type=origin.get("chat_type") or "p2p",
            chat_name=origin.get("chat_name") or "", sender_id=origin.get("sender_id") or "", sender_type="user",
            sender_name=origin.get("sender_name") or "", tenant_key=origin.get("tenant_key") or "",
            text=origin.get("text") or "", created_at=time.time(), thread_id=origin.get("thread_id") or "",
            mentions_me=True, at_all=False, msg_type="text", deleted=False)
        revision = int(draft.get("revision") or 0) + 1
        self._revisions[anchor] = {"revision": revision, "notes": notes, "origin": origin}
        self._last_by_chat[inb.chat_id] = anchor
        chat = self.settings.for_chat(inb.chat_id)
        source = shape_source(self.build_source, inb, chat, thread_root=await self._thread_roots.root_for(inb, chat))
        event = MessageEvent(
            text=_REVISION_PROMPT.format(user_name=self._me.name, notes=notes, content=draft.get("content", "")),
            message_type=MessageType.TEXT, source=source,
            message_id=f"{anchor}#rev{revision}", reply_anchor_override=anchor or None,
            # The same system prompt as the conversation's other turns: a change would break its cache.
            channel_prompt=chat.instructions or None,
            user_id=inb.sender_id, user_name=inb.sender_name or None, allow_gateway_control=False)
        logger.info("lark_user: revision %d for %s chat=%s", revision, anchor, hash_for_log(inb.chat_id))
        await self.handle_message(event)

    async def _on_review_click(self, event: dict[str, Any]) -> None:
        try:
            await self._desk.handle_card(event)
        except LarkCliError as exc:
            logger.warning("lark_user: review click could not be completed (%s: %s)", exc.kind, exc)

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
        if reply_to and reply_to.startswith(_CONTROL_ID):
            logger.debug("lark_user: dropped the answer to a plugin-run command in %s", hash_for_log(chat_id))
            return SendResult(success=True, message_id=_DROPPED)
        if not self._active:
            # Switched off (/lark-user stop): no results at all, neither a reply nor a draft. Turns in
            # flight were interrupted; whatever they still emit (the /stop notice included) ends here.
            logger.info("lark_user: auto-reply is off; dropped output for %s (%d chars)", hash_for_log(chat_id),
                        len(content))
            return SendResult(success=True, message_id=_DROPPED)
        review = self.settings.for_chat(chat_id).reply.delivery == "review" and chat_id != self._desk.chat_id
        draft = self._draft_for(chat_id, reply_to)
        try:
            if review:
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


def _never_auto_enabled(config) -> bool:
    """lark_user has no credentials to find, so nothing may switch it on by itself: it replies under
    the user's own name, and only an explicit ``platforms.lark_user.enabled: true`` opts in.

    ``is_connected`` is probed on a transient ``enabled=True`` view (``gateway.config_env`` start-up
    sweep, ``hermes gateway setup``), so ``_enabled`` here would be True on every machine that has
    ``lark-cli`` on PATH."""
    return False


def register(ctx):
    ctx.register_command(
        "lark-user", lark_user_command,
        description="Lark personal auto-reply: start / stop / status (in memory; off after every gateway start), "
                    "reset <chat_id> to end a chat's conversations, reply.<key> <value> to change a reply "
                    "setting in memory until the next restart",
        args_hint="start|stop|status|reset [chat_id]|reply[.<key> <value>|--reset]")
    ctx.register_platform(
        name="lark_user",
        label="Lark (personal)",
        dashboard_description="Auto-reply to your own Lark direct messages and @mentions, as you, via lark-cli.",
        docs_url="https://hermes-agent.nousresearch.com/docs/user-guide/messaging/lark-user",
        adapter_factory=LarkUserAdapter,
        check_fn=check_requirements,
        is_connected=_never_auto_enabled,
        config_connected_fn=_enabled,
        install_hint="Install lark-cli, then log in as yourself for Hermes: `HERMES_HOME=<home> lark-cli auth login`",
        max_message_length=0,  # the adapter chunks itself so the prefix/suffix land once
        emoji="🪶",
        allow_update_command=False,
        display_tier="minimal",
        platform_hint=PLATFORM_HINT,
        **gateway_env.AUTHZ,
    )
