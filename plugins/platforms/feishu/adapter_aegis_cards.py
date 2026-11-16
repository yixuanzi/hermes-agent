"""Feishu CardKit card output for one agent turn (Aegis, FEISHU_CARD_OUTPUT).

Inside a turn the reply and its execution chrome render as one card (feishu_cardkit); each
delegated remote agent gets its own card; any CardKit failure returns the route to text/post."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import replace as _dc_replace
from pathlib import Path
from typing import Any, Dict, Optional

from gateway.platforms.base import (
    MessageEvent,
    ProcessingOutcome,
    SendResult,
)

logger = logging.getLogger("plugins.platforms.feishu.adapter")


def _fa():
    """The adapter module, read at call time (no import cycle; SDK names bind at connect)."""
    import sys

    return sys.modules["plugins.platforms.feishu.adapter"]

# Card output (see feishu_cardkit.py).  One CardKit card per speaker per
# turn: header title, collapsible execution trace, streamed rich-text body.
_DEFAULT_CARD_FLUSH_INTERVAL = 0.4


# Metadata key the gateway sets on tool-progress / thinking sends so the
# adapter can tell execution chrome from the reply itself.  Absent means
# "this is content", which keeps every other caller on the body path.
_CARD_PROGRESS_METADATA_KEY = "hermes_progress"


# Metadata key a caller sets to keep a send out of the card entirely.  For
# transient status notices — a liveness heartbeat, an interaction ack — which
# are not the agent's reply and would otherwise splice themselves into the
# middle of whatever answer is streaming.  The platform-neutral
# ``non_conversational`` marker is honoured the same way.
_CARD_BYPASS_METADATA_KEY = "hermes_card_bypass"


_card_output_module: Any = None


def _cardkit_module() -> Any:
    """Return the card-output engine module (:mod:`feishu_cardkit`).

    Imported through a helper rather than a top-level ``from .`` because
    the plugin-adapter test loader execs this file by path under a
    package-less module name, where relative imports do not resolve.
    Falling back to a by-path load also avoids re-entering the package
    ``__init__``, which would import a second copy of this module.
    """
    global _card_output_module
    if _card_output_module is not None:
        return _card_output_module
    try:
        from . import feishu_cardkit as module  # type: ignore[import-not-found]
    except ImportError:
        import importlib.util
        import sys as _sys

        path = Path(__file__).with_name("feishu_cardkit.py")
        spec = importlib.util.spec_from_file_location("hermes_feishu_cardkit", str(path))
        if spec is None or spec.loader is None:  # pragma: no cover — defensive
            raise ImportError(f"Could not load {path}")
        module = importlib.util.module_from_spec(spec)
        _sys.modules.setdefault(spec.name, module)
        spec.loader.exec_module(module)
    _card_output_module = module
    return module


def _card_copy_from_env() -> Any:
    """Build the card's user-visible strings, honouring env overrides.

    Deployments localize or rebrand the card without touching code:
    ``FEISHU_CARD_TITLE``, ``FEISHU_CARD_DELEGATE_TITLE`` (may contain
    ``{agent}``), ``FEISHU_CARD_TRACE_TITLE`` and
    ``FEISHU_CARD_ATTACHMENT_NOTE`` (may contain ``{names}``).
    """
    defaults = _cardkit_module().CardCopy()
    overrides: dict[str, Any] = {}
    for env_name, field_name in (
        ("FEISHU_CARD_TITLE", "main_title"),
        ("FEISHU_CARD_DELEGATE_TITLE", "delegate_title"),
        ("FEISHU_CARD_TRACE_TITLE", "trace_title"),
        ("FEISHU_CARD_ATTACHMENT_NOTE", "attachment_note"),
    ):
        raw = os.getenv(env_name, "").strip()
        if raw:
            overrides[field_name] = raw
    if not overrides:
        return defaults

    return _dc_replace(defaults, **overrides)


class FeishuCardOutputMixin:
    """Mixed into ``FeishuAdapter`` ahead of ``BasePlatformAdapter``."""

    def _card_output_active(self) -> bool:
        manager = getattr(self, "_card_manager", None)
        return manager is not None and manager.available()

    def _card_begin_turn(self, event: MessageEvent) -> None:
        """Open the card window for this turn.

        Only output produced inside a turn becomes a card; a slash-command
        reply or a cron push would otherwise turn into a card whose
        execution panel is permanently empty.
        """
        manager = getattr(self, "_card_manager", None)
        if manager is None:
            return
        source = getattr(event, "source", None)
        thread_id = getattr(source, "thread_id", None)
        try:
            manager.begin_turn(
                chat_id=str(getattr(source, "chat_id", "") or ""),
                thread_id=thread_id,
                reply_to=event.message_id if thread_id else None,
                metadata={"thread_id": thread_id} if thread_id else None,
            )
        except Exception:
            logger.debug("[Feishu] card begin_turn failed", exc_info=True)

    async def _card_finish_turn(
        self, event: MessageEvent, outcome: ProcessingOutcome
    ) -> None:
        """Seal the turn's card: collapse the trace, stop the streaming animation."""
        manager = getattr(self, "_card_manager", None)
        if manager is None:
            return
        source = getattr(event, "source", None)
        try:
            await manager.finish_turn(
                chat_id=str(getattr(source, "chat_id", "") or ""),
                thread_id=getattr(source, "thread_id", None),
                failed=outcome is ProcessingOutcome.FAILURE,
            )
        except Exception:
            logger.debug("[Feishu] card finish_turn failed", exc_info=True)
        finally:
            getattr(self, "_delegate_card_blocks", {}).clear()
            getattr(self, "_delegate_card_locks", {}).clear()
            getattr(self, "_delegate_card_streamed", set()).clear()
            # The attachment ledger is per-turn: "send me that file again"
            # in a later turn has to upload it again.
            getattr(self, "_card_media_dispatched", {}).clear()

    @staticmethod
    def _card_bypass_requested(metadata: Optional[dict[str, Any]]) -> bool:
        """Whether this send asked to skip the card and go out as a message.

        ``hermes_card_bypass`` is the explicit opt-out.  ``non_conversational``
        is the gateway's existing name for a lifecycle/status send, which is
        the same thing by another route — a notice about the session rather
        than a turn of the conversation.

        Tool-progress sends are excluded: those are execution chrome and have
        a home inside the card's collapsible panel.
        """
        if not metadata:
            return False
        if metadata.get(_CARD_PROGRESS_METADATA_KEY):
            return False
        return bool(
            metadata.get(_CARD_BYPASS_METADATA_KEY)
            or metadata.get("non_conversational")
        )

    def _card_delegate_owner(self, session_id: Any) -> str:
        """Owner tag for a delegated remote agent.

        Distinct A2A context ids get distinct owners, so two delegations in
        one turn land on two cards; the same delegation keeps writing to its
        own card across turns of a foreground loop.
        """
        token = str(session_id or "").strip()
        return f"delegate:{token}" if token else "delegate:anonymous"

    async def _send_via_card(
        self,
        *,
        chat_id: str,
        content: str,
        reply_to: Optional[str],
        metadata: Optional[dict[str, Any]],
        owner: str = "main",
        title: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> Optional[SendResult]:
        """Append ``content`` to the live card, or ``None`` to fall back.

        The returned ``message_id`` is a card *block* handle, not a Feishu
        message id: the gateway streams a reply by sending and then editing,
        and ``_edit_via_card`` maps the handle back onto the block so the edit
        rewrites exactly that text.
        """
        if not self._card_output_active():
            return None
        # Transient status notices stay out of the card: they are not part of
        # the answer, and appending them would interleave a heartbeat or an
        # acknowledgement into the text the user is reading.  ``kind`` being
        # set means an internal caller has already decided where this belongs
        # (delegate trace / body), so the opt-out does not apply to it.
        if kind is None and self._card_bypass_requested(metadata):
            return None
        manager = self._card_manager
        resolved_kind = kind or (
            "trace"
            if (metadata or {}).get(_CARD_PROGRESS_METADATA_KEY)
            else "body"
        )
        try:
            delivered = await manager.deliver(
                chat_id=chat_id,
                content=self.format_message(content),
                kind=resolved_kind,
                owner=owner,
                title=title,
                reply_to=reply_to,
                metadata=metadata,
            )
        except Exception:
            logger.warning("[Feishu] card delivery failed; using text/post", exc_info=True)
            return None
        if delivered is None:
            return None
        block_id, _card_message_id = delivered
        return SendResult(success=True, message_id=block_id)

    async def _edit_via_card(self, message_id: str, content: str) -> Optional[SendResult]:
        """Rewrite a card block.  ``None`` when ``message_id`` is not one."""
        manager = getattr(self, "_card_manager", None)
        if manager is None or not manager.owns_message(message_id):
            return None
        try:
            edited = await manager.edit(
                message_id=message_id, content=self.format_message(content)
            )
        except Exception:
            logger.warning("[Feishu] card edit failed", exc_info=True)
            edited = False
        if edited is None:
            return None
        if not edited:
            # The block's card was already sealed (turn finished, or the body
            # rolled onto a continuation card).  Its text is on screen either
            # way, and reporting failure here would make the gateway re-send
            # the same text as a brand-new bubble.
            logger.debug("[Feishu] card block %s is sealed; edit ignored", message_id)
        return SendResult(success=True, message_id=message_id)

    async def _close_card_sessions(self) -> None:
        """Seal every live card so a shutdown never leaves one 'generating'."""
        manager = getattr(self, "_card_manager", None)
        if manager is None:
            return
        try:
            await manager.close_all()
        except Exception:
            logger.debug("[Feishu] card teardown failed", exc_info=True)
        finally:
            getattr(self, "_delegate_card_blocks", {}).clear()
            getattr(self, "_delegate_card_locks", {}).clear()
            getattr(self, "_delegate_card_streamed", set()).clear()
            getattr(self, "_card_media_dispatched", {}).clear()

    @staticmethod
    def _is_card_block_handle(value: Any) -> bool:
        """True for the synthetic ``hermes-card:`` id card-mode sends return.

        It stands for a block inside a card, not a Feishu message, so it must
        never reach the reply API — Feishu rejects it with a bare field
        validation error.
        """
        return str(value or "").startswith(_cardkit_module().BLOCK_ID_PREFIX)

    @staticmethod
    def _is_card_entity_payload(msg_type: str, payload: Any) -> bool:
        """True for an interactive message that carries a CardKit ``card_id``.

        A card entity binds to exactly one message, so such a payload is never
        re-sent as a fallback: a second send of an already-bound entity fails
        with "card binding biz count over limit" however it is addressed.
        """
        if msg_type != "interactive":
            return False
        try:
            data = json.loads(payload)
        except (TypeError, ValueError):
            return False
        return bool(
            isinstance(data, dict)
            and data.get("type") == "card"
            and isinstance(data.get("data"), dict)
            and data["data"].get("card_id")
        )

    async def handle_delegate_card_event(
        self,
        *,
        chat_id: str,
        metadata: Optional[dict[str, Any]],
        owner: str,
        agent_name: Optional[str],
        event_type: str,
        content: str,
    ) -> bool:
        """Render one delegate stream event into that delegate's own card.

        Returns False when cards are unavailable, which leaves the caller on
        the historical per-event message path.
        """
        if not self._card_output_active():
            return False
        manager = self._card_manager
        title = manager.title_for(owner, agent_name=agent_name)
        states = getattr(self, "_delegate_card_blocks", None)
        if states is None:
            states = {}
            self._delegate_card_blocks = states

        async def _append(text: str, kind: str) -> bool:
            result = await self._send_via_card(
                chat_id=chat_id,
                content=text,
                reply_to=None,
                metadata=metadata,
                owner=owner,
                title=title,
                kind=kind,
            )
            return result is not None and result.success

        if event_type == "ai_delta":
            if not content:
                return True
            # Accumulate BEFORE awaiting anything.  This runs to completion
            # without yielding, so concurrent delta tasks cannot interleave
            # here and every render below writes the full accumulation — no
            # fragment is lost or reordered, and each update stays a prefix
            # superset of the last, which is what animates the typewriter.
            state = states.get(owner)
            if state is None:
                state = {"block_id": None, "text": ""}
                states[owner] = state
            state["text"] += content
            self._delegate_card_streamed_owners().add(owner)

            async with self._delegate_card_lock(owner):
                # Re-read under the lock: a task that queued behind the one
                # that opened the block must extend it, not open another.
                live = states.get(owner)
                if live is not state:
                    # A segment boundary (tool call / turn end) landed while
                    # this task waited; the text it carried is already
                    # rendered, so there is nothing left to do.
                    return True
                if live["block_id"] is None:
                    result = await self._send_via_card(
                        chat_id=chat_id, content=live["text"], reply_to=None,
                        metadata=metadata, owner=owner, title=title, kind="body",
                    )
                    if result is None or not result.success:
                        states.pop(owner, None)
                        return False
                    live["block_id"] = result.message_id
                    return True
                edited = await self._edit_via_card(live["block_id"], live["text"])
                return edited is not None and edited.success

        if event_type == "ai":
            async with self._delegate_card_lock(owner):
                # Turn-final for this delegate.  Deltas already streamed the
                # text into the body, so appending the final response on top
                # of them would show the answer twice — only a delegate that
                # never streamed at all needs it appended here.
                states.pop(owner, None)
                streamed = self._delegate_card_streamed_owners()
                if content and owner not in streamed:
                    await _append(content, "body")
                streamed.discard(owner)
                # Seal the card: one card per exchange with the remote agent.
                # A foreground loop holds a single A2A context for all its
                # turns, so the owner tag never changes and nothing else would
                # close it — every follow-up answer would pile into the first
                # card.
                await manager.close_owner(chat_id=chat_id, metadata=metadata, owner=owner)
            return True

        if event_type == "tool_call":
            from plugins.platforms.feishu.adapter_aegis_delegate import _FeishuDelegateOutputAdapter

            async with self._delegate_card_lock(owner):
                states.pop(owner, None)  # tool boundary ends the text segment
                return await _append(_FeishuDelegateOutputAdapter._format_tool_call(content), "trace")

        if event_type == "status":
            # Transport plumbing — "entered foreground loop", "return to main"
            # and friends describe the delegation machinery, not the user's
            # task.  The agent narrates the outcome itself (the a2a tool hands
            # it loop_exit_reason and the final response), so swallow these
            # rather than putting loop bookkeeping in the chat.  Reported as
            # handled so the legacy message path does not render them either.
            logger.debug("[Feishu] dropping delegate status in card mode: %s", content)
            return True

        if event_type == "error":
            async with self._delegate_card_lock(owner):
                states.pop(owner, None)
                return await _append(f"⚠️ `delegate error`: {content}", "body")

        return False

    def _delegate_card_lock(self, owner: str) -> asyncio.Lock:
        """Serialize card writes for one delegate.

        Every delegate event is scheduled as its own task, so without this an
        opening burst of deltas each opens its own body block.
        """
        locks = getattr(self, "_delegate_card_locks", None)
        if locks is None:
            locks = {}
            self._delegate_card_locks = locks
        lock = locks.get(owner)
        if lock is None:
            lock = asyncio.Lock()
            locks[owner] = lock
        return lock

    def _delegate_card_streamed_owners(self) -> set:
        """Owners that streamed body text during the current exchange."""
        streamed = getattr(self, "_delegate_card_streamed", None)
        if streamed is None:
            streamed = set()
            self._delegate_card_streamed = streamed
        return streamed
