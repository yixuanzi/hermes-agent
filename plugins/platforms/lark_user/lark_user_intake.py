"""Which polled messages become turns.

``drop_reason`` returns why a message is NOT dispatched (logged at DEBUG), or None to dispatch.
The user's own messages never become turns: replies are sent under the user's name and would
otherwise loop. The one exception, review commands in the user's own chat, is routed before this.
"""

from __future__ import annotations

import collections
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .lark_user_config import LarkUserSettings
from .lark_user_normalize import Inbound


@dataclass(frozen=True)
class Identity:
    open_id: str
    name: str
    tenant_key: str
    user_id: str = ""  # tenant-wide id, the same in every app; "" when the login cannot read it


def looks_like_own_reply(text: str, prefix: str) -> bool:
    marker = prefix.strip()
    head = text.lstrip()
    return bool(marker) and (head.startswith(marker) or head.startswith(f'<card title="{marker}'))


def drop_reason(inb: Inbound, source: str, *, me: Identity, settings: LarkUserSettings, now: float,
                dm_allowed: Callable[[str], bool], group_allowed: Callable[[str], bool]) -> Optional[str]:
    if inb.deleted:
        return "deleted"
    if inb.sender_id == me.open_id:
        return "self"
    if inb.sender_type != "user" and not settings.allow_bots:
        return "bot"
    if source == "dm":
        if inb.chat_type != "p2p":
            return "not_p2p"
        if not settings.poll.dm.enabled:
            return "dm_disabled"
    else:
        if inb.chat_type != "group":
            return "not_group"
        if not inb.mentions_me and not (inb.at_all and settings.poll.include_at_all):
            return "at_all" if inb.at_all else "not_mentioned"
    chat = settings.for_chat(inb.chat_id)
    if not chat.enabled:
        return "chat_disabled"
    if not settings.allow_external and me.tenant_key and inb.tenant_key and inb.tenant_key != me.tenant_key:
        return "external"
    if looks_like_own_reply(inb.text, chat.reply.prefix):
        return "echo"
    # create_time is minute-precision: allow one extra minute before calling a message stale.
    if inb.created_at and now - inb.created_at > settings.max_message_age + 60:
        return "stale"
    if source == "dm" and not dm_allowed(inb.sender_id):
        return "dm_policy"
    if source == "mention" and not group_allowed(inb.chat_id):
        return "group_policy"
    return None


class ChatRateLimit:
    """At most ``limit`` dispatched messages per chat in any 60s window (0 = unlimited)."""

    def __init__(self, limit: int, clock: Callable[[], float] = time.monotonic):
        self._limit = limit
        self._clock = clock
        self._hits: dict[str, collections.deque] = collections.defaultdict(collections.deque)

    def allow(self, chat_id: str) -> bool:
        if not self._limit:
            return True
        now, hits = self._clock(), self._hits[chat_id]
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True
