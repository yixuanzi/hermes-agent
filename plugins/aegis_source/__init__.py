"""``<source>`` sender-identity header for agent-bound Slack/Feishu turns (Aegis fork).

Each Slack/Feishu turn with a trusted sender carries one compact JSON header the model reads to
tell who is asking (platform, channel, user id, display name)::

    <source>{"platform":"feishu","channel":"oc_…","uid":"ou_…","uname":"Ada"}</source>

It rides ``pre_llm_call`` context, so it is appended to the turn's user message on the wire and
persisted with it (``api_content``, cache-safe on replay), while the visible transcript, slash
commands and ``/codex`` raw passthrough see the user's own text. The identity comes from the turn's
gateway session (``HERMES_SESSION_*``): the actor is the trusted sender of THIS turn, set even in a
shared chat whose route carries no user. ``uname`` is always present (possibly ``""``). This header
is separate from the remote A2A envelope (``plugins.aegis_a2a``), which carries parent identity only.
"""

from __future__ import annotations

import json
from typing import Optional

_SOURCE_HEADER_PLATFORMS = frozenset({"slack", "feishu"})


def source_header(platform: str, channel: str, user_id: str, user_name: str) -> Optional[str]:
    """``<source>{...}</source>`` for a Slack/Feishu sender with a trusted user id, else None."""
    if platform not in _SOURCE_HEADER_PLATFORMS or not user_id:
        return None
    from gateway.session import neutralize_untrusted_inline_text

    data: dict = {"platform": platform}
    if channel:
        data["channel"] = channel
    data["uid"] = user_id
    data["uname"] = neutralize_untrusted_inline_text(user_name or "")
    return f"<source>{json.dumps(data, ensure_ascii=False, separators=(',', ':'))}</source>"


def turn_source_header(platform: str = "") -> Optional[str]:
    """The header for the gateway turn bound on this thread (None outside a Slack/Feishu turn)."""
    from gateway.session_context import get_session_env

    return source_header(
        get_session_env("HERMES_SESSION_PLATFORM") or platform,
        get_session_env("HERMES_SESSION_CHAT_ID"),
        get_session_env("HERMES_SESSION_ACTOR_ID") or get_session_env("HERMES_SESSION_USER_ID"),
        get_session_env("HERMES_SESSION_ACTOR_NAME") or get_session_env("HERMES_SESSION_USER_NAME"),
    )


def on_pre_llm_call(platform: str = "", **_kwargs):
    header = turn_source_header(platform)
    return {"context": header} if header else None


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
