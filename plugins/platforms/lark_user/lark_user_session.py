"""Isolation policy -> ``SessionSource`` shape.

The key itself always comes from ``gateway.session.build_session_key`` (via the adapter's
``_event_session_key``); a policy only decides which source fields carry a value:

| policy                | chat_type | thread_id                  | shared_session |
|-----------------------|-----------|----------------------------|----------------|
| dm per_chat           | dm        | -                          | -              |
| dm per_message        | dm        | message_id                 | -              |
| group per_chat        | group     | -                          | True           |
| group per_chat_user   | group     | -                          | - (gateway ``group_sessions_per_user``) |
| group per_thread      | group     | topic id, else message_id  | -              |
| group per_message     | group     | message_id                 | -              |
"""

from __future__ import annotations

import dataclasses
from typing import Any, Callable

from .lark_user_config import ChatSettings
from .lark_user_normalize import Inbound


def thread_for(inbound: Inbound, chat: ChatSettings) -> str:
    if inbound.chat_type != "group":
        return inbound.message_id if chat.dm_scope == "per_message" else ""
    if chat.group_scope == "per_thread":
        return inbound.thread_id or inbound.message_id
    return inbound.message_id if chat.group_scope == "per_message" else ""


def shape_source(build_source: Callable[..., Any], inbound: Inbound, chat: ChatSettings) -> Any:
    is_group = inbound.chat_type == "group"
    source = build_source(
        chat_id=inbound.chat_id,
        chat_name=inbound.chat_name or None,
        chat_type="group" if is_group else "dm",
        user_id=inbound.sender_id,
        user_name=inbound.sender_name or None,
        thread_id=thread_for(inbound, chat) or None,
        message_id=inbound.message_id,
    )
    if is_group and chat.group_scope == "per_chat":
        source = dataclasses.replace(source, shared_session=True)
    return source
