"""Gateway turn wiring for the fork's ``aegis_a2a_delegate`` foreground delegation.

Two seams the turn runner calls: stopping a delegation when the turn is interrupted, and binding
the adapter's delegate output/input runtime onto the agent that will run this turn.
"""

from __future__ import annotations

import logging
import threading
from contextvars import copy_context
from typing import Any, Optional

logger = logging.getLogger(__name__)


def cancel_running_delegation(agent: Any) -> None:
    """Stop the A2A delegation ``agent`` is blocked in, if any, without blocking the caller.

    A delegate call blocks the turn on a remote task or on its foreground loop's input, and
    neither watches the interrupt flag; cancelling lets the turn reach its interrupt check. A
    remote cancel may block, so it runs on a daemon thread like the turn-process reaper.
    """
    from tools.a2a_delegate_tool import cancel_active_a2a_delegate

    threading.Thread(
        target=copy_context().run,
        args=(cancel_active_a2a_delegate, agent),
        name="gateway-a2a-delegate-cancel",
        daemon=True,
    ).start()


def _clear_delegate_foreground_runtime(agent: Any) -> None:
    agent._delegate_ext_output_adapter = None
    agent._delegate_ext_input_factory = None


def _delegate_thread_ts(adapter: Any, source: Any, event_message_id: Optional[str]) -> Optional[str]:
    """Slack DMs whose top-level messages are sessions key the loop on the triggering message."""
    thread_ts = source.thread_id
    platform_value = str(getattr(source.platform, "value", source.platform)).lower()
    if thread_ts or platform_value != "slack" or str(source.chat_type or "").lower() != "dm":
        return thread_ts
    extra = getattr(getattr(adapter, "config", None), "extra", {}) or {}
    return event_message_id if extra.get("dm_top_level_threads_as_sessions", True) else thread_ts


def bind_delegate_foreground_runtime_for_turn(
    runner: Any, agent: Any, source: Any, *, event_message_id: Optional[str] = None,
) -> None:
    """Point ``agent``'s delegate output/input at this turn's adapter route.

    Runs every turn on fresh and cached agents alike, so a cached agent never keeps the previous
    thread's or user's foreground queue. Adapters opt in with ``build_delegate_foreground_runtime``.
    """
    _clear_delegate_foreground_runtime(agent)
    adapters = getattr(runner, "adapters", {}) or {}
    adapter = adapters.get(source.platform) or adapters.get(str(source.platform))
    builder = getattr(adapter, "build_delegate_foreground_runtime", None)
    if not callable(builder):
        return
    try:
        runtime = builder(
            channel_id=source.chat_id,
            thread_ts=_delegate_thread_ts(adapter, source, event_message_id),
            user_id=getattr(source, "user_id", None),
            chat_type=getattr(source, "chat_type", None),
        )
    except Exception:
        logger.debug("Could not build delegate foreground runtime", exc_info=True)
        return
    if isinstance(runtime, dict):
        agent._delegate_ext_output_adapter = runtime.get("output")
        agent._delegate_ext_input_factory = runtime.get("input_factory")
