"""Platform-neutral helpers for adapters that host an ``aegis_a2a_delegate`` foreground loop.

Feishu and Slack both hand a chat to a remote A2A delegate loop and route its follow-ups before
normal dispatch. The rules for what a loop must not swallow, how a follow-up steers the remote
turn still running, and whether a session is already busy live here once, so timeout,
acknowledgement and steerable-input semantics cannot drift between the two adapters.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# The delegate foreground loop's own controls; they stay with the loop.
_DELEGATE_LOOP_CONTROLS = frozenset({"main", "exit"})

# Upper bound on waiting for a remote steer acknowledgement before the follow-up falls back to the
# loop's next-turn queue. Longer than the steer request's own HTTP timeout, so it settles first.
DELEGATE_STEER_WAIT_SECONDS = 15.0
DELEGATE_STEER_ACK = "⏩ Steered the remote agent's current task. Your message arrives after its next tool call."


def is_gateway_command_during_delegate_loop(text: Optional[str]) -> bool:
    """True when *text* must reach the gateway, not a delegate loop.

    A resolvable gateway command such as ``/stop`` or ``/new`` is handled outside the busy queue
    even while a loop owns the chat; only the loop's ``/main`` and ``/exit`` stay with the loop.
    Parsing matches ``MessageEvent.get_command``.
    """
    stripped = str(text or "").lstrip()
    if not stripped.startswith("/"):
        return False
    command = stripped.split(maxsplit=1)[0][1:].lower()
    if "@" in command:
        command = command.split("@", 1)[0]
    if not command or "/" in command or command in _DELEGATE_LOOP_CONTROLS:
        return False
    from hermes_cli.commands import should_bypass_active_session

    return should_bypass_active_session(command)


class DelegateLoopAdapterMixin:
    """Mixed into adapters (ahead of ``BasePlatformAdapter``) that host delegate foreground loops."""

    async def _steer_delegate_turn(
        self,
        input_adapter: Any,
        text: str,
        *,
        chat_id: str,
        metadata: Optional[dict[str, Any]],
    ) -> bool:
        """Fold a delegate-loop follow-up into the remote turn still running.

        No interrupt and no new turn. ``input_adapter`` is the loop's foreground input, bound to
        its A2A session. Commands are never steering text, and any refusal or failure returns
        False so the caller keeps the text for the loop's next-turn queue.
        """
        cleaned = str(text or "").strip()
        if not cleaned or cleaned.startswith("/"):
            return False
        schedule = getattr(input_adapter, "schedule_steer", None)
        future = schedule(cleaned) if callable(schedule) else None
        if future is None:
            return False
        try:
            accepted = bool(await asyncio.wait_for(asyncio.wrap_future(future), DELEGATE_STEER_WAIT_SECONDS))
        except Exception:
            logger.warning("[%s] Delegate steer failed; queueing as next turn", self.name, exc_info=True)
            return False
        if not accepted:
            return False
        try:
            await self.send(chat_id, DELEGATE_STEER_ACK, metadata=metadata)
        except Exception:
            logger.debug("[%s] Failed to send delegate steer acknowledgement", self.name, exc_info=True)
        return True

    def session_has_running_task(self, session_key: str) -> bool:
        """Is a turn already running for ``session_key``?

        The question an adapter must ask before starting an unprompted turn: the normal inbound
        path queues follow-ups, but a turn nobody asked for should not be queued either. Heals a
        stale lock first so one leaked guard cannot silence a session permanently.
        """
        active = getattr(self, "_active_sessions", None) or {}
        if not session_key or session_key not in active:
            return False
        self._heal_stale_session_lock(session_key)
        return session_key in self._active_sessions
