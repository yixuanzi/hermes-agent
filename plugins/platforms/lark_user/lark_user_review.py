"""Review mode: a reply waits as a draft in the user's own p2p chat until the user releases it.

Commands are plain messages the user sends in that chat (card buttons need a bot event
subscription a user token cannot have):

  /send [n]            send draft n (default: newest)
  /drop [n]            discard draft n (default: newest)
  /edit n <new text>   send <new text> instead of draft n
  /list                list pending drafts
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Awaitable, Callable, Optional

from .lark_user_cli import LarkCli, LarkCliError
from .lark_user_cursor import CursorStore
from .lark_user_render import neutralize_mentions

logger = logging.getLogger(__name__)

_COMMAND_RE = re.compile(r"^\s*/(send|drop|edit|list)\b[ \t]*(\d+)?[ \t]*(.*)$", re.DOTALL | re.IGNORECASE)
_SNIPPET = 200

# (draft, content) -> error text or "" on success
Deliver = Callable[[dict[str, Any], str], Awaitable[str]]


def parse_command(text: str) -> Optional[tuple[str, Optional[str], str]]:
    match = _COMMAND_RE.match(text or "")
    if not match:
        return None
    verb, number, rest = match.groups()
    return verb.lower(), number, rest.strip()


def _snippet(text: str) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= _SNIPPET else text[:_SNIPPET] + "…"


class ReviewDesk:
    def __init__(self, *, cli: LarkCli, store: CursorStore, my_open_id: str, review_chat: str, ttl: float,
                 deliver: Deliver):
        self._cli = cli
        self._store = store
        self._me = my_open_id
        self._configured_chat = review_chat
        self._ttl = ttl
        self._deliver = deliver

    @property
    def chat_id(self) -> str:
        return self._configured_chat or self._store.review_chat

    async def _notify(self, text: str) -> str:
        sent = await self._cli.send(text, fmt="post", chat_id=self.chat_id, user_id="" if self.chat_id else self._me)
        if not self.chat_id and sent.chat_id:
            self._store.review_chat = sent.chat_id  # the user's own p2p chat, learned from the first send
        return sent.message_id

    async def submit(self, draft: dict[str, Any], content: str) -> str:
        """Park ``content`` as a draft; returns the review message id."""
        number = str(self._store.next_draft)
        self._store.next_draft += 1
        self._store.drafts[number] = {**draft, "content": content, "created_at": time.time()}
        self._store.save()
        origin = draft.get("origin") or {}
        lines = [
            f"**📝 待审核回复 #{number}**",
            f"来自：{origin.get('sender_name') or origin.get('sender_id') or '?'}"
            + (f"（群：{origin['chat_name']}）" if origin.get("chat_name") else "（私聊）"),
            f"原消息：{neutralize_mentions(_snippet(origin.get('text', '')))}",
            "---",
            neutralize_mentions(content),
            "---",
            f"`/send {number}` 发送 · `/drop {number}` 丢弃 · `/edit {number} 新的回复内容` 修改后发送",
        ]
        return await self._notify("\n".join(lines))

    def _pick(self, number: Optional[str]) -> Optional[str]:
        if number:
            return number if number in self._store.drafts else None
        return max(self._store.drafts, key=int, default=None)

    async def handle(self, text: str) -> bool:
        """Run a review command; False when ``text`` is not one."""
        command = parse_command(text)
        if command is None:
            return False
        verb, number, rest = command
        if verb == "list":
            pending = sorted(self._store.drafts.items(), key=lambda kv: int(kv[0]))
            body = "\n".join(f"#{n} → {(d.get('origin') or {}).get('sender_name') or '?'}：{_snippet(d['content'])}"
                             for n, d in pending) or "没有待审核的回复"
            await self._notify(neutralize_mentions(body))
            return True
        picked = self._pick(number)
        if picked is None:
            await self._notify(f"找不到待审核回复 #{number}" if number else "没有待审核的回复")
            return True
        if verb == "edit" and not rest:
            await self._notify(f"`/edit {picked} 新的回复内容`：缺少新内容")
            return True
        draft = self._store.drafts.pop(picked)
        if verb == "drop":
            self._store.save()
            await self._notify(f"🗑️ #{picked} 已丢弃")
            return True
        error = await self._deliver(draft, rest if verb == "edit" else draft["content"])
        if error:
            self._store.drafts[picked] = draft  # keep it so the user can retry
            self._store.save()
            await self._notify(f"❌ #{picked} 发送失败：{error}")
        else:
            self._store.save()
            await self._notify(f"✅ #{picked} 已发送")
        return True

    async def expire(self, now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        stale = [n for n, d in self._store.drafts.items() if now - float(d.get("created_at") or 0) > self._ttl]
        if not stale:
            return
        for number in stale:
            self._store.drafts.pop(number, None)
        self._store.save()
        try:
            await self._notify("⌛ 已过期丢弃：" + "、".join(f"#{n}" for n in sorted(stale, key=int)))
        except LarkCliError as exc:
            logger.warning("lark_user: could not report expired drafts (%s)", exc.kind)
