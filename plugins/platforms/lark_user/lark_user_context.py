"""``context``: recent group messages handed to the agent with an @mention.

Read through lark-cli as the USER, so any group the user is in works (no bot membership). The
block rides ``MessageEvent.channel_context``, which the gateway puts before ``[New message]``:

  <group_messages>            the chat's newest ``limit`` messages within ``hours``
  [11-20 14:19] Alice: ...
  </group_messages>
  <thread_messages>           scope: thread, when the mention sits in a topic (omt_*)
  ...
  </thread_messages>

Message text is untrusted: each line is flattened and stripped of ``<``/``>`` so it cannot close
the wrapper or forge another block. Any fetch failure yields no context, never a failed turn.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any, Iterable

from .lark_user_cli import LarkCli, LarkCliError
from .lark_user_config import ContextSettings
from .lark_user_normalize import Inbound

logger = logging.getLogger(__name__)


def _clean(text: str, max_chars: int) -> str:
    flat = " ".join(str(text or "").split()).replace("<", "‹").replace(">", "›")
    return flat if not max_chars or len(flat) <= max_chars else flat[:max_chars] + "…"


def _line(item: dict[str, Any], max_chars: int) -> str:
    sender = item.get("sender") or {}
    name = _clean(sender.get("name") or sender.get("id") or "unknown", 64)
    who = f"[bot] {name}" if sender.get("sender_type") == "app" else name
    stamp = str(item.get("create_time") or "")[5:16]  # "YYYY-MM-DD HH:MM" -> "MM-DD HH:MM"
    return f"[{stamp}] {who}: {_clean(item.get('content', ''), max_chars)}"


def _block(tag: str, items: Iterable[dict[str, Any]], *, skip: set[str], limit: int, max_chars: int) -> tuple[str, set[str]]:
    """Newest-first ``items`` -> chronological ``<tag>`` block of up to ``limit`` usable messages."""
    picked = [m for m in items
              if not m.get("deleted") and str(m.get("content") or "").strip() and m.get("message_id") not in skip][:limit]
    if not picked:
        return "", set()
    lines = [_line(m, max_chars) for m in reversed(picked)]
    return f"<{tag}>\n" + "\n".join(lines) + f"\n</{tag}>", {str(m.get("message_id")) for m in picked}


async def group_context(cli: LarkCli, inb: Inbound, settings: ContextSettings) -> str:
    skip = {inb.message_id}
    since = dt.datetime.fromtimestamp(time.time() - settings.hours * 3600).astimezone().isoformat(timespec="seconds")
    try:
        chat_items = await cli.chat_messages(inb.chat_id, start=since, page_size=min(50, settings.limit + 1))
        group, shown = _block("group_messages", chat_items, skip=skip, limit=settings.limit,
                              max_chars=settings.msg_max_chars)
        thread = ""
        if settings.scope == "thread" and settings.thread_limit and inb.thread_id.startswith("omt_"):
            thread_items = await cli.thread_messages(inb.thread_id, page_size=min(50, settings.thread_limit + 1))
            thread, _ = _block("thread_messages", thread_items, skip=skip | shown, limit=settings.thread_limit,
                               max_chars=settings.msg_max_chars)
    except LarkCliError as exc:
        logger.warning("lark_user: group context unavailable (%s)", exc.kind)
        return ""
    return "\n\n".join(part for part in (group, thread) if part)
