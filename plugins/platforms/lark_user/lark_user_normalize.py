"""``im +messages-search`` items -> ``Inbound`` records, plus the intake filter.

The CLI already renders ``content`` to text (mentions substituted by display name, cards flattened
to ``<card ...>``); ``create_time`` is local wall-clock ``YYYY-MM-DD HH:MM``. ``--is-at-me`` also
returns @all messages, so "mentions me" is decided here from ``mentions``.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any, Optional

_AT_ALL_RE = re.compile(r'<at\s+user_id="all"\s*>.*?</at>', re.IGNORECASE)


@dataclass(frozen=True)
class Inbound:
    message_id: str
    chat_id: str
    chat_type: str  # "p2p" | "group"
    chat_name: str
    sender_id: str
    sender_type: str  # "user" | "app"
    sender_name: str
    tenant_key: str
    text: str
    created_at: float
    thread_id: str
    mentions_me: bool
    at_all: bool
    msg_type: str
    deleted: bool


def parse_create_time(value: Any) -> float:
    """``"2026-11-20 22:25"`` (local time) or epoch milliseconds -> epoch seconds; 0.0 if unknown."""
    text = str(value or "").strip()
    if text.isdigit():
        return int(text) / 1000.0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    return 0.0


def _strip_my_mention(text: str, my_name: str) -> str:
    if my_name:
        text = text.replace(f"@{my_name}", "")
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def to_inbound(item: dict[str, Any], *, my_open_id: str, my_name: str) -> Optional[Inbound]:
    message_id = str(item.get("message_id") or "")
    chat_id = str(item.get("chat_id") or "")
    if not message_id or not chat_id:
        return None
    sender = item.get("sender") or {}
    mentions = item.get("mentions") or []
    content = str(item.get("content") or "")
    mentions_me = any(str(m.get("id") or "") == my_open_id for m in mentions if isinstance(m, dict))
    return Inbound(
        message_id=message_id,
        chat_id=chat_id,
        chat_type=str(item.get("chat_type") or ""),
        chat_name=str(item.get("chat_name") or ""),
        sender_id=str(sender.get("id") or ""),
        sender_type=str(sender.get("sender_type") or ""),
        sender_name=str(sender.get("name") or ""),
        tenant_key=str(sender.get("tenant_key") or ""),
        text=_strip_my_mention(content, my_name) if mentions_me else content.strip(),
        created_at=parse_create_time(item.get("create_time")),
        thread_id=str(item.get("thread_id") or ""),
        mentions_me=mentions_me,
        at_all=bool(_AT_ALL_RE.search(content)),
        msg_type=str(item.get("msg_type") or ""),
        deleted=bool(item.get("deleted")),
    )
