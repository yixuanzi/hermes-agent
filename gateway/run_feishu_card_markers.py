"""Outbound metadata markers a Feishu card-rendering adapter reads (fork).

Feishu's card output mode renders one agent turn as one CardKit card, so it must tell execution
chrome (tool and thinking progress, which belongs in the card's collapsible panel) from the reply
and from transient notices. Both markers are Feishu-scoped, so no other platform's metadata changes
shape, and neither reaches conversation history.
"""

from __future__ import annotations

from typing import Any, Optional


def _is_feishu(platform: Any) -> bool:
    return str(getattr(platform, "value", platform) or "").strip().lower() == "feishu"


def progress_card_metadata(metadata: Optional[dict[str, Any]], *, platform: Any) -> Optional[dict[str, Any]]:
    """Tool-progress sends: non-conversational lifecycle metadata, plus ``hermes_progress`` on Feishu."""
    from gateway.run import _non_conversational_metadata

    marked = _non_conversational_metadata(metadata, platform=platform)
    return {**(marked or {}), "hermes_progress": True} if _is_feishu(platform) else marked


def heartbeat_card_metadata(metadata: Optional[dict[str, Any]], *, platform: Any) -> Optional[dict[str, Any]]:
    """The long-running heartbeat is a liveness signal, not part of the answer: on Feishu it bypasses
    the turn's card and goes out as an ordinary message (``hermes_card_bypass``)."""
    from gateway.run import _non_conversational_metadata

    marked = _non_conversational_metadata(metadata, platform=platform)
    return {**(marked or {}), "hermes_card_bypass": True} if _is_feishu(platform) else marked
