"""Outermost layers of an agent-bound Slack/Feishu turn (fork): ``<source>`` header and group history.

The structured header carries the trusted sender identity (platform, channel, user id, display
name) that the A2A delegate and user-identity paths read; it is built only when the adapter supplied
a user id, and it is separate from the remote A2A envelope, which carries parent identity only.
The adapter-supplied group history block sits directly under it, above the shared-session
``[Name]`` prefix, reply quote and media notes, so those keep their shape and the history stays one
detachable preamble.
"""

from __future__ import annotations

import json
from typing import Any, Optional

_SOURCE_HEADER_PLATFORMS = frozenset({"slack", "feishu"})


def source_header(source: Any) -> Optional[str]:
    """``<source>{...}</source>`` for Slack/Feishu senders with a trusted user id, else None."""
    platform = getattr(getattr(source, "platform", None), "value", None)
    if platform not in _SOURCE_HEADER_PLATFORMS or not getattr(source, "user_id", None):
        return None
    from gateway.session import neutralize_untrusted_inline_text

    data: dict = {"platform": platform}
    if source.chat_id:
        data["channel"] = source.chat_id
    data["uid"] = source.user_id
    # Always present (possibly ""): the executor's parser expects the key.
    data["uname"] = neutralize_untrusted_inline_text(source.user_name or "")
    return f"<source>{json.dumps(data, ensure_ascii=False, separators=(',', ':'))}</source>"


def add_source_preamble(event: Any, source: Any, message_text: str) -> str:
    """Prepend the event's group-history block, then the ``<source>`` header, to the prepared text."""
    metadata = getattr(event, "metadata", None)
    history = str(metadata.get("group_history_block") or "").strip() if isinstance(metadata, dict) else ""
    if history:
        message_text = f"{history}\n\n{message_text}"
    header = source_header(source)
    return f"{header}\n\n{message_text}" if header else message_text
