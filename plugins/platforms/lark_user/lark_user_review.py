"""Review mode: every reply waits as a draft until the user approves, revises or rejects it.

Two presentations (``review.interactive``):

card  The review bot (a separate Lark app) sends the user one card per draft with a notes input
      and three buttons: ✅ approve (send the reply), ✏️ revise (the notes go back to the agent,
      whose rewritten reply arrives as a new card), 🚫 reject (no reply at all).
text  Fallback without a review app: the draft is posted to the user's own chat and the user
      answers there with commands:
        /send [n]            approve draft n (default: newest)
        /drop [n]            reject draft n
        /edit n <text>       send <text> instead of draft n
        /revise n <notes>    have the agent rewrite draft n following <notes>
        /list                list pending drafts
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Awaitable, Callable, Optional, Protocol

from .lark_user_cli import LarkCli, LarkCliError, SentMessage
from .lark_user_cursor import CursorStore
from .lark_user_render import neutralize_mentions
from .lark_user_review_bot import ReviewBot
from .lark_user_review_card import ACTION_KEY, NOTES_FIELD, review_card

logger = logging.getLogger(__name__)

_COMMAND_RE = re.compile(r"^\s*/(send|drop|edit|revise|list)\b[ \t]*(\d+)?[ \t]*(.*)$", re.DOTALL | re.IGNORECASE)
_SNIPPET = 200

# (draft, content) -> error text, "" on success
Deliver = Callable[[dict[str, Any], str], Awaitable[str]]
# (draft, notes) -> None: start the agent's rewrite; its reply comes back through submit()
Revise = Callable[[dict[str, Any], str], Awaitable[None]]


def parse_command(text: str) -> Optional[tuple[str, Optional[str], str]]:
    match = _COMMAND_RE.match(text or "")
    if not match:
        return None
    verb, number, rest = match.groups()
    return verb.lower(), number, rest.strip()


def _snippet(text: str) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= _SNIPPET else text[:_SNIPPET] + "…"


class Presenter(Protocol):
    chat_id: str

    async def show(self, number: str, draft: dict[str, Any]) -> SentMessage: ...
    async def settle(self, number: str, draft: dict[str, Any], state: str, note: str = "",
                     token: str = "") -> None: ...


class CardPresenter:
    """Drafts as interactive cards from the review bot, in its p2p chat with the user."""

    def __init__(self, bot: ReviewBot, union_id: str):
        self._bot = bot
        self._union_id = union_id
        self.chat_id = ""  # learned from the first card; every click must come from this chat

    async def show(self, number: str, draft: dict[str, Any]) -> SentMessage:
        sent = await self._bot.send_card(self._union_id, review_card(number, draft))
        self.chat_id = self.chat_id or sent.chat_id
        return sent

    async def settle(self, number: str, draft: dict[str, Any], state: str, note: str = "",
                     token: str = "") -> None:
        card = review_card(number, draft, state=state, note=note)
        if token:  # answering a click
            try:
                await self._bot.update_after_click(token, card)
                return
            except LarkCliError as exc:
                logger.warning("lark_user: click-token card update failed (%s); patching the message", exc.kind)
        if draft.get("review_msg"):
            await self._bot.patch_card(draft["review_msg"], card)


_TEXT_STATES = {"sent": "✅ #{n} 已发送", "rejected": "🚫 #{n} 已拒绝，不回复", "expired": "⌛ #{n} 已过期，未回复",
                "revising": "✏️ #{n} 修订中，改好后会发来新的待审核回复", "failed": "❌ #{n} 发送失败：{note}",
                "need_note": "`/revise {n} 修改意见`：缺少修改意见",
                "paused": "自动回复已关闭，#{n} 暂不能修订：先发送 /lark-user start 开启"}


class TextPresenter:
    """Drafts as messages in the user's own p2p chat, sent with the user token."""

    def __init__(self, cli: LarkCli, store: CursorStore, *, my_open_id: str, review_chat: str):
        self._cli = cli
        self._store = store
        self._me = my_open_id
        self._configured_chat = review_chat

    @property
    def chat_id(self) -> str:
        return self._configured_chat or self._store.review_chat

    async def notice(self, text: str) -> SentMessage:
        sent = await self._cli.send(text, fmt="post", chat_id=self.chat_id, user_id="" if self.chat_id else self._me)
        if not self.chat_id and sent.chat_id:
            self._store.review_chat = sent.chat_id  # the user's own p2p chat, learned from the first send
        return sent

    async def show(self, number: str, draft: dict[str, Any]) -> SentMessage:
        origin = draft.get("origin") or {}
        lines = [
            f"**📝 待审核回复 #{number}**",
            f"来自：{origin.get('sender_name') or origin.get('sender_id') or '?'}"
            + (f"（群：{origin['chat_name']}）" if origin.get("chat_name") else "（私聊）"),
            f"原消息：{_snippet(origin.get('text', ''))}",
        ]
        if draft.get("revision"):
            lines.append(f"第 {draft['revision']} 次修订，意见：{_snippet(draft.get('revision_note', ''))}")
        lines += ["---", draft["content"], "---",
                  f"`/send {number}` 发送 · `/drop {number}` 拒绝 · `/revise {number} 修改意见` 修订 · "
                  f"`/edit {number} 新的回复内容` 改后发送"]
        return await self.notice(neutralize_mentions("\n".join(lines)))

    async def settle(self, number: str, draft: dict[str, Any], state: str, note: str = "",
                     token: str = "") -> None:
        await self.notice(neutralize_mentions(_TEXT_STATES[state].format(n=number, note=note)))


class ReviewDesk:
    def __init__(self, *, store: CursorStore, presenter: Presenter, ttl: float, deliver: Deliver, revise: Revise,
                 revising_allowed: Callable[[], bool]):
        self._store = store
        self.presenter = presenter
        self._ttl = ttl
        self._deliver = deliver
        self._revise = revise
        self._revising_allowed = revising_allowed  # a revision runs the agent again: only while switched on

    @property
    def chat_id(self) -> str:
        return self.presenter.chat_id

    async def submit(self, draft: dict[str, Any], content: str) -> str:
        """Park ``content`` as a draft and show it to the user; returns the review message id."""
        number = self._store.new_draft_number()
        record = {**draft, "content": content, "created_at": time.time()}
        self._store.put_draft(number, record)
        sent = await self.presenter.show(number, record)
        self._store.put_draft(number, {**record, "review_msg": sent.message_id, "review_chat": sent.chat_id})
        return sent.message_id

    # ── decisions ───────────────────────────────────────────────────────────

    async def approve(self, number: str, content: Optional[str] = None, token: str = "") -> None:
        draft = self._store.pop_draft(number)
        if content is not None:
            draft["content"] = content
        error = await self._deliver(draft, draft["content"])
        if error:
            self._store.put_draft(number, draft)  # keep it so the user can retry
            await self.presenter.settle(number, draft, "failed", error, token=token)
        else:
            await self.presenter.settle(number, draft, "sent", f"已于 {time.strftime('%H:%M')} 发送", token=token)

    async def reject(self, number: str, token: str = "") -> None:
        draft = self._store.pop_draft(number)
        await self.presenter.settle(number, draft, "rejected", token=token)

    async def revise(self, number: str, notes: str, token: str = "") -> None:
        draft = self._store.draft(number)
        if not self._revising_allowed():
            await self.presenter.settle(number, draft, "paused", token=token)
            return
        if not notes.strip():
            await self.presenter.settle(number, draft, "need_note", token=token)
            return
        self._store.pop_draft(number)
        await self.presenter.settle(number, draft, "revising", f"修订意见：{notes.strip()}\n改好后会发来新的待审核卡片。",
                                    token=token)
        await self._revise(draft, notes.strip())

    async def expire(self, now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        stale = [n for n, d in self._store.drafts.items() if now - float(d.get("created_at") or 0) > self._ttl]
        for number in sorted(stale, key=int):
            draft = self._store.pop_draft(number)
            try:
                await self.presenter.settle(number, draft, "expired")
            except LarkCliError as exc:
                logger.warning("lark_user: could not mark draft #%s expired (%s)", number, exc.kind)

    # ── inputs ──────────────────────────────────────────────────────────────

    async def handle_card(self, event: dict[str, Any]) -> None:
        """One review-card click. Only a click on a pending draft's own card, in the chat that card
        was sent to (the review bot's p2p chat with the user), acts on that draft. A decision removes
        the draft, so a redelivered or repeated click finds nothing to act on."""
        try:
            value = json.loads(event.get("action_value") or "{}")
            form = json.loads(event.get("form_value") or "{}")
        except ValueError:
            return
        verb, number = str(value.get(ACTION_KEY) or ""), str(value.get("draft") or "")
        draft = self._store.draft(number)
        if (verb not in {"approve", "revise", "reject"} or draft is None
                or draft.get("review_msg") != event.get("message_id") or draft.get("review_chat") != event.get("chat_id")):
            logger.debug("lark_user: ignored review click %s on #%s", verb or "?", number or "?")
            return
        token = str(event.get("token") or "")
        if verb == "approve":
            await self.approve(number, token=token)
        elif verb == "reject":
            await self.reject(number, token=token)
        else:
            await self.revise(number, str(form.get(NOTES_FIELD) or ""), token=token)

    async def handle_text(self, text: str) -> bool:
        """Run a text review command; False when ``text`` is not one."""
        command = parse_command(text)
        if command is None:
            return False
        verb, number, rest = command
        notice = getattr(self.presenter, "notice", None)
        if verb == "list":
            pending = sorted(self._store.drafts.items(), key=lambda kv: int(kv[0]))
            body = "\n".join(f"#{n} → {(d.get('origin') or {}).get('sender_name') or '?'}：{_snippet(d['content'])}"
                             for n, d in pending) or "没有待审核的回复"
            await notice(neutralize_mentions(body))
            return True
        picked = number if number in self._store.drafts else (
            None if number else max(self._store.drafts, key=int, default=None))
        if picked is None:
            await notice(f"找不到待审核回复 #{number}" if number else "没有待审核的回复")
        elif verb == "edit" and not rest:
            await notice(f"`/edit {picked} 新的回复内容`：缺少新内容")
        elif verb in {"send", "edit"}:
            await self.approve(picked, rest if verb == "edit" else None)
        elif verb == "drop":
            await self.reject(picked)
        else:
            await self.revise(picked, rest)
        return True
