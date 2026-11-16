"""Outermost layer of an agent-bound Slack/Feishu turn (fork): the ``<source>`` header.

The structured header carries the trusted sender identity (platform, channel, user id, display
name) that the A2A delegate and user-identity paths read; it is built only when the adapter supplied
a user id, and it is separate from the remote A2A envelope, which carries parent identity only.
Feishu's recent group history is not added here: it rides ``MessageEvent.channel_context``, which
the shared inbound path renders as ``{history}\n\n[New message]\n...``.
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
    """Prepend the ``<source>`` header to the prepared text."""
    header = source_header(source)
    return f"{header}\n\n{message_text}" if header else message_text
