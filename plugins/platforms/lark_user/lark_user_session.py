"""Isolation policy -> ``SessionSource`` shape.

The key itself always comes from ``gateway.session.build_session_key`` (via the adapter's
``_event_session_key``); a policy only decides which source fields carry a value:

| policy                | chat_type | thread_id                  | shared_session |
|-----------------------|-----------|----------------------------|----------------|
| dm per_chat           | dm        | -                          | -              |
| dm per_message        | dm        | message_id                 | -              |
| group per_chat        | group     | -                          | True           |
| group per_chat_user   | group     | -                          | - (gateway ``group_sessions_per_user``) |
| group per_thread      | group     | topic's root message id    | -              |
| group per_message     | group     | message_id                 | -              |

``per_thread`` keys a conversation by the message its topic hangs off. A root @mention is keyed
by its own id before anyone replies to it; once a topic grows under it, the in-topic @mentions
carry the topic id (``omt_…``), which ``ThreadRoots`` maps back to that same root message, so the
root mention and its topic are one session.

Senders cannot ``/new`` (they have no gateway control), so ``session.idle_reset`` and
``session.daily_reset_at`` end a conversation for them: ``expiry_reason`` decides, and the adapter
suspends the stale session so the gateway opens a fresh one for the message that found it stale.
"""

from __future__ import annotations

import collections
import dataclasses
import datetime as dt
import logging
from typing import Any, Awaitable, Callable

from .lark_user_cli import LarkCliError
from .lark_user_config import ChatSettings
from .lark_user_normalize import Inbound

logger = logging.getLogger(__name__)

_ROOT_CACHE = 2000


class ThreadRoots:
    """Topic id -> the id of the message the topic hangs off, looked up once per topic."""

    def __init__(self, lookup: Callable[[str], Awaitable[str]]):
        self._lookup = lookup
        self._roots: collections.OrderedDict[str, str] = collections.OrderedDict()

    async def root_for(self, inbound: Inbound, chat: ChatSettings) -> str:
        """The ``per_thread`` key message of ``inbound``; "" when the policy does not use one."""
        if inbound.chat_type != "group" or chat.group_scope != "per_thread":
            return ""
        topic = inbound.thread_id
        if not topic.startswith("omt_"):
            return inbound.message_id  # not in a topic (yet): the message is its own root
        if topic not in self._roots:
            try:
                root = await self._lookup(topic)
            except LarkCliError as exc:
                logger.warning("lark_user: topic root lookup failed (%s); keying the session by the topic", exc.kind)
                return topic
            if not root:
                return topic
            self._roots[topic] = root
            while len(self._roots) > _ROOT_CACHE:
                self._roots.popitem(last=False)
        return self._roots[topic]


def thread_for(inbound: Inbound, chat: ChatSettings, thread_root: str = "") -> str:
    if inbound.chat_type != "group":
        return inbound.message_id if chat.dm_scope == "per_message" else ""
    if chat.group_scope == "per_thread":
        return thread_root or inbound.thread_id or inbound.message_id
    return inbound.message_id if chat.group_scope == "per_message" else ""


def shape_source(build_source: Callable[..., Any], inbound: Inbound, chat: ChatSettings, *,
                 thread_root: str = "") -> Any:
    is_group = inbound.chat_type == "group"
    source = build_source(
        chat_id=inbound.chat_id,
        chat_name=inbound.chat_name or None,
        chat_type="group" if is_group else "dm",
        user_id=inbound.sender_id,
        user_name=inbound.sender_name or None,
        thread_id=thread_for(inbound, chat, thread_root) or None,
        message_id=inbound.message_id,
    )
    if is_group and chat.group_scope == "per_chat":
        source = dataclasses.replace(source, shared_session=True)
    return source


def labels_speakers(source: Any, extra: dict[str, Any]) -> bool:
    """Whether the gateway prefixes each message of ``source``'s session with ``[speaker name]``:
    a session shared across participants (the rule of ``gateway.session.is_shared_multi_user_session``,
    with the adapter's own key settings). Elsewhere the name sits in the system prompt instead."""
    if source.chat_type == "dm":
        return False
    if getattr(source, "shared_session", False):
        return True
    per_user = (extra.get("thread_sessions_per_user", False) if source.thread_id
                else extra.get("group_sessions_per_user", True))
    return not per_user


def expiry_reason(last_active: float, now: float, chat: ChatSettings) -> str:
    """Why a conversation last active at ``last_active`` is over at ``now``; "" while it goes on."""
    if chat.idle_reset and now - last_active >= chat.idle_reset:
        return "idle"
    if chat.daily_reset_at is not None:
        today = dt.datetime.fromtimestamp(now)
        boundary = today.replace(hour=chat.daily_reset_at // 60, minute=chat.daily_reset_at % 60,
                                 second=0, microsecond=0)
        if boundary > today:
            boundary -= dt.timedelta(days=1)
        if last_active < boundary.timestamp():
            return "daily"
    return ""
