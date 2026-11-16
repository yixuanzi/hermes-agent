"""Slack foreground I/O for the fork's ``aegis_a2a_delegate`` loops (Aegis).

A delegate loop takes over a Slack conversation: follow-ups go to the remote agent instead of the
main session, the remote's streamed output is rendered in the thread, and remote approval/clarify
requests become Block Kit controls answered through the authenticated A2A responder — never the
local gateway approval/clarify state. Everything is keyed by Slack session dimensions (channel,
thread, user, chat type) and dispatched on the adapter's event loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from gateway.platforms.base import (
    DelegateLoopAdapterMixin,
    SendResult,
    is_gateway_command_during_delegate_loop,
)

try:  # sibling module; support both package and flat plugin-dir import
    from .block_kit import sanitize_blocks
except ImportError:  # pragma: no cover - plugin loaded outside package context
    from block_kit import sanitize_blocks  # type: ignore

logger = logging.getLogger(__name__)

DEFAULT_DELEGATE_STREAM_EDIT_INTERVAL = 3.0
DELEGATE_ACTION_PREFIX = "a2a_delegate|"
_DELEGATE_INTERACTIONS_MAX = 1000
_INTERACTION_EVENTS = frozenset({"approval_request", "clarify_request", "approval_resolved", "clarify_resolved"})


@dataclass
class _SlackDelegateRoute:
    key: str
    channel_id: str
    thread_ts: Optional[str]
    user_id: Optional[str]
    chat_type: Optional[str]
    input_adapter: Any


@dataclass
class _SlackDelegateStreamState:
    accumulated_text: str = ""
    pending_text: str = ""
    message_id: Optional[str] = None
    current_groups: list[str] = field(default_factory=list)
    message_ids: list[str] = field(default_factory=list)
    can_edit: bool = True
    lock: Optional[asyncio.Lock] = None
    last_flush_ts: float = 0.0
    flush_task: Optional[asyncio.Task] = None


class _SlackDelegateInputAdapter:
    """Blocking foreground input for one delegate loop; a fresh one per runtime-factory call."""

    def __init__(self, adapter: Any, route: _SlackDelegateRoute):
        self._adapter = adapter
        self._route = route
        self._condition = threading.Condition()
        self._lines: list[str] = []
        self._closed = False
        self._waiting_for_input = False
        self._last_read_timed_out = False
        self._a2a_session = None

    def enter_foreground(self) -> bool:
        self._adapter._register_delegate_route(self._route)
        return True

    def exit_foreground(self) -> None:
        self._adapter._unregister_delegate_route(self._route)

    def bind_a2a_session(self, session) -> None:
        with self._condition:
            self._a2a_session = session

    def unbind_a2a_session(self, session) -> None:
        with self._condition:
            if self._a2a_session is session:
                self._a2a_session = None

    def schedule_steer(self, text: str):
        """Steer the remote turn in flight; ``None`` means queue as next turn."""
        with self._condition:
            session = self._a2a_session
        schedule = getattr(session, "schedule_steer", None)
        return schedule(text) if callable(schedule) else None

    def push_line(self, text: str) -> bool:
        with self._condition:
            if self._closed:
                return False
            self._waiting_for_input = False
            self._lines.append(str(text or ""))
            self._condition.notify_all()
            return True

    def read_line(self, timeout=None):
        with self._condition:
            self._last_read_timed_out = False
            deadline = None if timeout is None else time.monotonic() + max(0.0, float(timeout))
            while not self._lines and not self._closed:
                self._waiting_for_input = True
                self._condition.notify_all()
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._waiting_for_input = False
                    self._last_read_timed_out = True
                    self._condition.notify_all()
                    return None
                self._condition.wait(timeout=remaining)
            self._waiting_for_input = False
            if self._lines:
                line = self._lines.pop(0)
                self._condition.notify_all()
                return line
            return None

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._waiting_for_input = False
            self._condition.notify_all()
        self.exit_foreground()

    def last_read_timed_out(self) -> bool:
        with self._condition:
            return self._last_read_timed_out

    def is_waiting_for_input(self) -> bool:
        with self._condition:
            return self._waiting_for_input


def _format_delegate_tool_call_content(content: str) -> str:
    raw = str(content or "").strip()
    if not raw:
        return ""
    tool_name, _sep, raw_args = raw.partition(" ")
    tool_name = tool_name.strip() or "tool"
    preview = " ".join(raw_args.strip().split())
    if not preview:
        return f"`tool` {tool_name}"
    if len(preview) > 80:
        preview = preview[:77] + "..."
    return f"`tool` {tool_name}: {preview}"


class _SlackDelegateOutputAdapter:
    """Renders a delegate's events into the Slack route it was built for."""

    def __init__(self, adapter: Any, *, channel_id: str, thread_ts: Optional[str],
                 user_id: Optional[str] = None, chat_type: Optional[str] = None):
        self._adapter = adapter
        self._channel_id = channel_id
        self._thread_ts = thread_ts
        self._user_id = user_id
        self._chat_type = chat_type
        self._a2a_session = None

    def bind_a2a_interaction_session(self, session) -> None:
        self._a2a_session = session

    def unbind_a2a_interaction_session(self, session) -> None:
        if self._a2a_session is session:
            self._a2a_session = None

    def emit(self, source, event_type, content, session_id=None) -> None:
        self._adapter._schedule_delegate_output(
            self._emit_async(str(source or ""), str(event_type or ""), str(content or ""), session_id=session_id))

    def _route_key(self) -> str:
        return self._adapter._delegate_route_key(
            channel_id=self._channel_id, thread_ts=self._thread_ts, user_id=self._user_id, chat_type=self._chat_type)

    # Kept as a method for callers that format tool lines through the output adapter.
    _format_delegate_tool_call_content = staticmethod(_format_delegate_tool_call_content)

    async def _emit_interaction(self, event_type: str, content: str, metadata: Optional[dict]) -> None:
        try:
            payload = json.loads(content)
        except (TypeError, ValueError):
            payload = {}
        if not payload:
            return
        responder = getattr(self._a2a_session, "schedule_interaction_response", None)
        responder = responder if callable(responder) else None
        if event_type == "approval_request":
            await self._adapter.send_delegate_exec_approval(
                chat_id=self._channel_id, payload=payload, responder=responder, metadata=metadata,
                user_id=self._user_id)
        elif event_type == "clarify_request":
            await self._adapter.send_delegate_clarify(
                chat_id=self._channel_id, payload=payload, responder=responder, metadata=metadata,
                user_id=self._user_id)
        else:
            await self._adapter.resolve_delegate_interaction(payload)

    async def _emit_async(self, source: str, event_type: str, content: str, *, session_id=None) -> None:
        del session_id
        metadata = {"thread_id": self._thread_ts} if self._thread_ts else None
        if source == "delegate" and event_type in _INTERACTION_EVENTS:
            await self._emit_interaction(event_type, content, metadata)
            return
        route_key = self._route_key()
        if source == "delegate" and event_type == "ai_delta":
            if content:
                await self._adapter.handle_delegate_ai_delta(
                    route_key=route_key, chat_id=self._channel_id, content=content, metadata=metadata)
            return
        if source == "delegate" and event_type in {"ai", "tool_call"}:
            await self._adapter.handle_delegate_stream_segment_break(
                route_key=route_key, chat_id=self._channel_id, metadata=metadata)
            rendered = _format_delegate_tool_call_content(content) if event_type == "tool_call" else ""
            if rendered:
                await self._adapter.send(self._channel_id, rendered, metadata=metadata)
            return
        prefix = {"status": "_delegate_", "error": "`delegate error`"}.get(event_type, f"`{event_type}`")
        await self._adapter.send(self._channel_id, f"{prefix}: {content}", metadata=metadata)


class SlackDelegateStreamMixin:
    """Per-route streaming of a delegate's AI deltas: edit one message at most every few seconds."""

    def _delegate_stream_effective_limit(self) -> int:
        try:
            raw_limit = int(getattr(self, "MAX_MESSAGE_LENGTH", 39000) or 39000)
        except (TypeError, ValueError):
            raw_limit = 39000
        return max(1, raw_limit - (64 if raw_limit > 128 else 0))

    def _delegate_stream_rendered_length(self, text: str) -> int:
        try:
            rendered = self.format_message(text)
        except Exception:
            rendered = text
        return self.message_len_fn(rendered)

    def _split_delegate_stream_content(self, text: str) -> list[str]:
        remaining = str(text or "")
        limit = self._delegate_stream_effective_limit()
        groups: list[str] = []
        while remaining:
            if self._delegate_stream_rendered_length(remaining) <= limit:
                groups.append(remaining)
                break
            lo, hi = 1, len(remaining)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if self._delegate_stream_rendered_length(remaining[:mid]) <= limit:
                    lo = mid
                else:
                    hi = mid - 1
            split_at = max(1, lo)
            groups.append(remaining[:split_at])
            remaining = remaining[split_at:]
        return groups

    def _get_delegate_stream_state(self, route_key: str) -> _SlackDelegateStreamState:
        with self._delegate_routes_lock:
            state = self._delegate_stream_states.get(route_key)
            if state is None:
                state = _SlackDelegateStreamState()
                self._delegate_stream_states[route_key] = state
        if state.lock is None:
            state.lock = asyncio.Lock()
        return state

    @staticmethod
    def _reset_delegate_stream_segment_locked(state: _SlackDelegateStreamState) -> None:
        task = state.flush_task
        if task is not None and not task.done():
            task.cancel()
        state.flush_task = None
        state.accumulated_text = ""
        state.pending_text = ""
        state.message_id = None
        state.current_groups = []
        state.message_ids = []
        state.can_edit = True
        state.last_flush_ts = 0.0

    def _clear_delegate_stream_state_by_key(self, route_key: Optional[str]) -> None:
        if not route_key:
            return
        with self._delegate_routes_lock:
            state = self._delegate_stream_states.pop(route_key, None)
        if state is not None and state.flush_task is not None and not state.flush_task.done():
            state.flush_task.cancel()

    def _delegate_stream_edit_interval_seconds(self) -> float:
        try:
            raw = getattr(self, "_delegate_stream_edit_interval", DEFAULT_DELEGATE_STREAM_EDIT_INTERVAL)
            return max(0.0, float(raw or 0.0))
        except (TypeError, ValueError):
            return DEFAULT_DELEGATE_STREAM_EDIT_INTERVAL

    async def _flush_delegate_stream_after_delay(
        self, *, route_key: str, chat_id: str, metadata: Optional[dict[str, Any]], delay: float,
    ) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            state = self._delegate_stream_states.get(route_key)
            if state is None or state.lock is None:
                return
            async with state.lock:
                if state.flush_task is not asyncio.current_task():
                    return
                state.flush_task = None
                await self._flush_delegate_stream_locked(
                    state=state, chat_id=chat_id, metadata=metadata, route_key=route_key, force=True)
        except asyncio.CancelledError:
            return

    def _defer_delegate_flush(self, state, *, chat_id, metadata, route_key) -> bool:
        """Throttle edits of the same message: schedule one delayed flush and report True."""
        interval = self._delegate_stream_edit_interval_seconds()
        elapsed = time.monotonic() - state.last_flush_ts
        if elapsed >= interval:
            return False
        if route_key and (state.flush_task is None or state.flush_task.done()):
            state.flush_task = asyncio.create_task(self._flush_delegate_stream_after_delay(
                route_key=route_key, chat_id=chat_id, metadata=metadata, delay=interval - elapsed))
        return True

    async def _send_first_delegate_groups(self, state, groups, *, chat_id, metadata) -> None:
        latest_message_id: Optional[str] = None
        sent_groups: list[str] = []
        message_ids: list[str] = []
        for group in groups:
            result = await self.send(chat_id, group, metadata=metadata)
            if not result.success:
                return
            latest_message_id = str(result.message_id) if result.message_id else latest_message_id
            sent_groups.append(group)
            if latest_message_id:
                message_ids.append(latest_message_id)
        state.message_id = latest_message_id
        state.current_groups = sent_groups[-1:]
        state.message_ids = message_ids[-1:]
        state.accumulated_text = sent_groups[-1] if sent_groups else ""
        state.pending_text = ""
        state.last_flush_ts = time.monotonic()

    async def _edit_single_delegate_group(self, state, group, *, chat_id, metadata) -> None:
        previous_text = state.current_groups[-1] if state.current_groups else ""
        if group != previous_text and state.message_id:
            result = await self.edit_message(chat_id, state.message_id, group)
            if not result.success:
                result = await self.send(chat_id, state.pending_text, metadata=metadata)
                if result.success and result.message_id:
                    state.message_id = str(result.message_id)
            state.current_groups = [group]
            state.accumulated_text = group
            state.last_flush_ts = time.monotonic()
        state.pending_text = ""

    async def _roll_delegate_groups(self, state, groups, *, chat_id, metadata) -> None:
        """Past Slack's single-message limit: finish the current message, send the rest separately."""
        latest_message_id: Optional[str] = state.message_id
        if state.message_id:
            edit_result = await self.edit_message(chat_id, state.message_id, groups[0])
            if not edit_result.success:
                return
        for group in groups[1:]:
            result = await self.send(chat_id, group, metadata=metadata)
            if not result.success:
                return
            if result.message_id:
                latest_message_id = str(result.message_id)
        state.message_id = latest_message_id
        state.current_groups = groups[-1:]
        state.message_ids = [latest_message_id] if latest_message_id else []
        state.accumulated_text = groups[-1]
        state.pending_text = ""
        state.last_flush_ts = time.monotonic()

    async def _flush_delegate_stream_locked(
        self, *, state: _SlackDelegateStreamState, chat_id: str, metadata: Optional[dict[str, Any]],
        route_key: Optional[str] = None, force: bool = False,
    ) -> None:
        if not state.pending_text and state.message_id:
            return
        if state.message_id and state.pending_text and not force and self._defer_delegate_flush(
                state, chat_id=chat_id, metadata=metadata, route_key=route_key):
            return
        if state.flush_task is not None and not state.flush_task.done():
            state.flush_task.cancel()
            state.flush_task = None
        groups = self._split_delegate_stream_content(state.accumulated_text)
        if not groups:
            return
        if not state.message_id:
            await self._send_first_delegate_groups(state, groups, chat_id=chat_id, metadata=metadata)
        elif len(groups) == 1:
            await self._edit_single_delegate_group(state, groups[0], chat_id=chat_id, metadata=metadata)
        else:
            await self._roll_delegate_groups(state, groups, chat_id=chat_id, metadata=metadata)

    async def handle_delegate_ai_delta(
        self, *, route_key: str, chat_id: str, content: str, metadata: Optional[dict[str, Any]],
    ) -> None:
        state = self._get_delegate_stream_state(route_key)
        async with state.lock:
            rendered = str(content or "")
            state.accumulated_text += rendered
            state.pending_text += rendered
            await self._flush_delegate_stream_locked(
                state=state, chat_id=chat_id, metadata=metadata, route_key=route_key)

    async def handle_delegate_stream_segment_break(
        self, *, route_key: str, chat_id: str, metadata: Optional[dict[str, Any]],
    ) -> None:
        state = self._delegate_stream_states.get(route_key)
        if state is None or state.lock is None:
            return
        async with state.lock:
            await self._flush_delegate_stream_locked(
                state=state, chat_id=chat_id, metadata=metadata, route_key=route_key, force=True)
            self._reset_delegate_stream_segment_locked(state)


class SlackDelegateInteractionMixin:
    """Remote A2A approval/clarify rendered as Block Kit; answers go to the remote responder."""

    async def _update_delegate_interaction_message(self, state: dict[str, Any], decision_text: str) -> None:
        message_ts = str(state.get("message_ts") or "")
        channel_id = str(state.get("channel_id") or "")
        if not message_ts or not channel_id:
            return
        original_text = str(state.get("question") or state.get("command") or "Delegate interaction")
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": original_text[:3000]}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": decision_text}]},
        ]
        try:
            await self._get_client(channel_id, team_id=state.get("team_id") or None).chat_update(
                channel=channel_id, ts=message_ts, text=decision_text, blocks=sanitize_blocks(blocks))
        except Exception:
            logger.warning("[Slack] Failed to update delegate interaction", exc_info=True)

    def _remember_delegate_interaction(self, interaction_id: str, state: dict[str, Any]) -> None:
        self._delegate_interactions[interaction_id] = state
        self._trim_oldest_dict_entries(self._delegate_interactions, _DELEGATE_INTERACTIONS_MAX)

    @staticmethod
    def _delegate_approval_actions(payload: dict[str, Any], interaction_id: str) -> list[dict[str, Any]]:
        prefix = f"{DELEGATE_ACTION_PREFIX}{interaction_id}|"

        def _button(text, action_id, value, style=None):
            button = {"type": "button", "text": {"type": "plain_text", "text": text}, "action_id": action_id,
                      "value": prefix + value}
            if style:
                button["style"] = style
            return button

        actions = [_button("Allow Once", "hermes_approve_once", "once", "primary"),
                   _button("Deny", "hermes_deny", "deny", "danger")]
        if payload.get("allow_session", True):
            actions.insert(1, _button("Allow Session", "hermes_approve_session", "session"))
        if payload.get("allow_permanent", True):
            actions.insert(-1, _button("Always Allow", "hermes_approve_always", "always"))
        return actions

    async def send_delegate_exec_approval(
        self, *, chat_id: str, payload: dict[str, Any], responder, metadata: Optional[dict[str, Any]] = None,
        user_id: Optional[str] = None,
    ) -> SendResult:
        """Render an A2A approval whose buttons answer the remote task."""
        interaction_id = str(payload.get("interaction_id") or "")
        if not interaction_id:
            return SendResult(success=False, error="Missing A2A interaction id")
        if not self._app:
            return SendResult(success=False, error="Not connected")
        team_id = self._metadata_team_id(metadata)
        chat_id = await self._ensure_dm_conversation(chat_id, team_id=team_id)
        try:
            thread_ts = self._resolve_thread_ts(None, metadata)
            command = str(payload.get("command") or "")
            description = str(payload.get("description") or "dangerous command")
            command_preview = command[:2400] + ("..." if len(command) > 2400 else "")
            blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": (
                    f"⚠️ *Delegate command approval required*\n```{command_preview}```\nReason: {description[:500]}")}},
                {"type": "actions", "elements": self._delegate_approval_actions(payload, interaction_id)},
            ]
            kwargs: dict[str, Any] = {"channel": chat_id, "text": f"⚠️ Delegate approval required: {command_preview[:100]}",
                                      "blocks": sanitize_blocks(blocks)}
            if thread_ts:
                kwargs["thread_ts"] = thread_ts
            result = await self._get_client(chat_id, team_id=team_id).chat_postMessage(**kwargs)
            message_ts = result.get("ts", "")
            self._remember_delegate_interaction(interaction_id, {
                "kind": "approval", "interaction_id": interaction_id, "responder": responder, "channel_id": chat_id,
                "thread_ts": thread_ts, "user_id": user_id, "team_id": team_id, "message_ts": message_ts,
                "command": command_preview, "resolved": False})
            return SendResult(success=True, message_id=message_ts, raw_response=result)
        except Exception as exc:
            logger.error("[Slack] send_delegate_exec_approval failed: %s", exc, exc_info=True)
            return SendResult(success=False, error=str(exc))

    @staticmethod
    def _delegate_clarify_blocks(question: str, choices: list[str], interaction_id: str, multi_select: bool):
        prefix = f"{DELEGATE_ACTION_PREFIX}{interaction_id}|"
        elements = [
            {"type": "button", "text": {"type": "plain_text", "text": choice[:75]},
             "action_id": f"hermes_clarify_choice_{idx}", "value": prefix + str(idx)}
            for idx, choice in enumerate(choices)
        ]
        elements.append({"type": "button", "text": {"type": "plain_text", "text": "✏️ Other…"},
                         "action_id": "hermes_clarify_other", "value": prefix + "other"})
        if multi_select:
            elements.append({"type": "button", "text": {"type": "plain_text", "text": "Submit selection"},
                             "style": "primary", "action_id": "hermes_clarify_submit", "value": prefix + "submit"})
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f"❓ {question[:2900]}"}}]
        for start in range(0, len(elements), 5):
            blocks.append({"type": "actions", "elements": elements[start:start + 5]})
        return blocks

    async def send_delegate_clarify(
        self, *, chat_id: str, payload: dict[str, Any], responder, metadata: Optional[dict[str, Any]] = None,
        user_id: Optional[str] = None,
    ) -> SendResult:
        """Render a remote clarify request, including multi-select controls."""
        interaction_id = str(payload.get("interaction_id") or "")
        if not interaction_id:
            return SendResult(success=False, error="Missing A2A interaction id")
        if not self._app:
            return SendResult(success=False, error="Not connected")
        team_id = self._metadata_team_id(metadata)
        chat_id = await self._ensure_dm_conversation(chat_id, team_id=team_id)
        try:
            thread_ts = self._resolve_thread_ts(None, metadata)
            question = str(payload.get("question") or "Clarification required")
            choices = [str(item) for item in (payload.get("choices") or []) if str(item).strip()]
            multi_select = bool(payload.get("multi_select"))
            kwargs: dict[str, Any] = {"channel": chat_id}
            if choices:
                kwargs["text"] = f"❓ Delegate clarification: {question[:100]}"
                kwargs["blocks"] = self._delegate_clarify_blocks(question, choices, interaction_id, multi_select)
            else:
                kwargs["text"] = f"❓ Delegate clarification: {question}"
            if thread_ts:
                kwargs["thread_ts"] = thread_ts
            result = await self._get_client(chat_id, team_id=team_id).chat_postMessage(**kwargs)
            state = {
                "kind": "clarify", "interaction_id": interaction_id, "responder": responder, "channel_id": chat_id,
                "thread_ts": thread_ts, "user_id": user_id, "team_id": team_id, "question": question,
                "choices": choices, "multi_select": multi_select, "selected": [], "awaiting_text": not choices,
                "resolved": False, "message_ts": result.get("ts", ""),
            }
            self._remember_delegate_interaction(interaction_id, state)
            return SendResult(success=True, message_id=state["message_ts"], raw_response=result)
        except Exception as exc:
            logger.error("[Slack] send_delegate_clarify failed: %s", exc, exc_info=True)
            return SendResult(success=False, error=str(exc))

    async def resolve_delegate_interaction(self, payload: dict[str, Any]) -> None:
        state = self._delegate_interactions.get(str(payload.get("interaction_id") or ""))
        if state is None:
            return
        state["resolved"] = True
        state["awaiting_text"] = False
        value = payload.get("resolved_value")
        await self._update_delegate_interaction_message(
            state, f"✅ Delegate interaction resolved: {str(value or 'completed')[:200]}")

    def _delegate_click_state(self, body, action, kind: str):
        """``(interaction_id, token, state, user_id)`` for an authorized click on a live interaction, else None."""
        parts = str(action.get("value") or "").split("|", 2)
        if len(parts) != 3:
            return None
        _prefix, interaction_id, token = parts
        state = self._delegate_interactions.get(interaction_id)
        if state is None or state.get("resolved") or state.get("kind") != kind:
            return None
        channel_id = str((body.get("channel") or {}).get("id") or "")
        msg_ts = str((body.get("message") or {}).get("ts") or "")
        user_id = str((body.get("user") or {}).get("id") or "")
        if channel_id != str(state.get("channel_id") or "") or msg_ts != str(state.get("message_ts") or ""):
            logger.warning("[Slack] Delegate %s callback context mismatch", kind)
            return None
        if state.get("user_id") and str(state["user_id"]) != user_id:
            logger.warning("[Slack] Delegate %s callback user mismatch", kind)
            return None
        if not self._is_interactive_user_authorized(
                user_id, channel_id=channel_id, team_id=self._event_team_id({}, body)):
            return None
        return interaction_id, token, state, user_id

    async def _handle_delegate_approval_action(self, body, action) -> None:
        clicked = self._delegate_click_state(body, action, "approval")
        if clicked is None:
            return
        interaction_id, choice, state, _user_id = clicked
        responder = state.get("responder")
        if not callable(responder) or not responder(interaction_id, "approval", choice):
            return
        state["resolved"] = True
        await self._update_delegate_interaction_message(state, f"✅ Delegate approval response sent: {choice}")

    async def _toggle_delegate_choice(self, state: dict[str, Any], token: str):
        """Resolve a choice token: the value to send, or None when the click only changed a multi-select."""
        try:
            choice = state.get("choices", [])[int(token)]
        except (ValueError, IndexError, TypeError):
            return None
        if not state.get("multi_select"):
            return choice
        selected = list(state.get("selected") or [])
        if choice in selected:
            selected.remove(choice)
        else:
            selected.append(choice)
        state["selected"] = selected
        await self._update_delegate_interaction_message(
            state, "Selected: " + (", ".join(selected) if selected else "none") + ".")
        return None

    async def _handle_delegate_clarify_action(self, body, action) -> None:
        clicked = self._delegate_click_state(body, action, "clarify")
        if clicked is None:
            return
        interaction_id, token, state, user_id = clicked
        if token == "other":
            state["awaiting_text"] = True
            await self._update_delegate_interaction_message(state, f"✏️ Awaiting typed answer from {user_id or 'user'}…")
            return
        if token == "submit":
            value_to_send: Any = list(state.get("selected") or [])
            if not value_to_send:
                await self._update_delegate_interaction_message(state, "Select at least one option before submitting.")
                return
        else:
            value_to_send = await self._toggle_delegate_choice(state, token)
            if value_to_send is None:
                return
        responder = state.get("responder")
        if not callable(responder) or not responder(interaction_id, "clarify", value_to_send):
            return
        state["resolved"] = True
        state["awaiting_text"] = False
        await self._update_delegate_interaction_message(state, "✅ Delegate clarification response sent.")

    async def _route_delegate_approval_action(self, ack, body, action) -> None:
        """Bolt handler for approval buttons: delegate clicks go to the remote responder."""
        if str(action.get("value") or "").startswith(DELEGATE_ACTION_PREFIX):
            await ack()
            await self._handle_delegate_approval_action(body, action)
            return
        await self._handle_approval_action(ack, body, action)

    async def _route_delegate_clarify_action(self, ack, body, action) -> None:
        """Bolt handler for clarify buttons: delegate clicks go to the remote responder."""
        if str(action.get("value") or "").startswith(DELEGATE_ACTION_PREFIX):
            await ack()
            await self._handle_delegate_clarify_action(body, action)
            return
        await self._handle_clarify_action(ack, body, action)


class SlackDelegateMixin(SlackDelegateStreamMixin, SlackDelegateInteractionMixin, DelegateLoopAdapterMixin):
    """Mixed into ``SlackAdapter`` ahead of its other bases."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Remote delegate interaction state is deliberately separate from the local gateway
        # approval/clarify maps: delegate buttons resolve a remote task, never the local queue.
        self._delegate_interactions: dict[str, dict[str, Any]] = {}
        self._delegate_routes: dict[str, _SlackDelegateRoute] = {}
        self._delegate_routes_lock = threading.RLock()
        self._delegate_loop: Optional[asyncio.AbstractEventLoop] = None
        self._delegate_stream_states: dict[str, _SlackDelegateStreamState] = {}

    def _delegate_route_key(self, *, channel_id: str, thread_ts: Optional[str], user_id: Optional[str],
                            chat_type: Optional[str]) -> str:
        try:
            from gateway.session import build_session_key

            source = self.build_source(chat_id=channel_id, chat_type=chat_type or "dm", user_id=user_id,
                                       thread_id=thread_ts)
            return build_session_key(source)
        except Exception:
            return json.dumps({"platform": "slack", "channel_id": channel_id, "thread_ts": thread_ts,
                               "user_id": user_id, "chat_type": chat_type}, sort_keys=True)

    def _register_delegate_route(self, route: _SlackDelegateRoute) -> None:
        with self._delegate_routes_lock:
            self._delegate_routes[route.key] = route

    def _unregister_delegate_route(self, route: _SlackDelegateRoute) -> None:
        with self._delegate_routes_lock:
            if self._delegate_routes.get(route.key) is route:
                self._delegate_routes.pop(route.key, None)
        self._clear_delegate_stream_state_by_key(route.key)

    def _get_delegate_route(self, *, channel_id: str, thread_ts: Optional[str], user_id: Optional[str] = None,
                            chat_type: Optional[str] = None) -> Optional[_SlackDelegateRoute]:
        key = self._delegate_route_key(channel_id=channel_id, thread_ts=thread_ts, user_id=user_id, chat_type=chat_type)
        with self._delegate_routes_lock:
            return self._delegate_routes.get(key)

    def _schedule_delegate_output(self, coro) -> None:
        loop = self._delegate_loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None and running is loop:
            running.create_task(coro)
            return
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, loop)
            return
        try:
            asyncio.run(coro)
        except RuntimeError:
            logger.debug("[Slack] Could not schedule delegate output", exc_info=True)

    def build_delegate_foreground_runtime(self, *, channel_id: str, thread_ts: Optional[str],
                                          user_id: Optional[str] = None, chat_type: Optional[str] = None) -> dict[str, Any]:
        """The gateway's per-turn hook: a fresh input adapter per factory call, one output adapter."""
        try:
            self._delegate_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        key = self._delegate_route_key(channel_id=channel_id, thread_ts=thread_ts, user_id=user_id, chat_type=chat_type)

        def _input_factory():
            route = _SlackDelegateRoute(key=key, channel_id=channel_id, thread_ts=thread_ts, user_id=user_id,
                                        chat_type=chat_type, input_adapter=None)
            route.input_adapter = _SlackDelegateInputAdapter(self, route)
            return route.input_adapter

        output_adapter = _SlackDelegateOutputAdapter(
            self, channel_id=channel_id, thread_ts=thread_ts, user_id=user_id, chat_type=chat_type)
        return {"output": output_adapter, "input_factory": _input_factory,
                "metadata": {"thread_id": thread_ts} if thread_ts else None}

    async def _maybe_route_delegate_foreground_message(
        self, *, text: str, channel_id: str, thread_ts: Optional[str], user_id: Optional[str],
        chat_type: Optional[str],
    ) -> bool:
        route = self._get_delegate_route(channel_id=channel_id, thread_ts=thread_ts, user_id=user_id,
                                         chat_type=chat_type)
        if route is None:
            return False
        input_adapter = getattr(route, "input_adapter", None)
        push_line = getattr(input_adapter, "push_line", None)
        if not callable(push_line):
            return False
        metadata = {"thread_id": thread_ts} if thread_ts else None
        await self.handle_delegate_stream_segment_break(route_key=route.key, chat_id=channel_id, metadata=metadata)
        if await self._steer_delegate_turn(input_adapter, text, chat_id=channel_id, metadata=metadata):
            return True
        if push_line(text):
            return True
        self._unregister_delegate_route(route)
        return False

    async def _maybe_route_delegate_interaction_message(
        self, *, text: str, channel_id: str, thread_ts: Optional[str], user_id: Optional[str],
        chat_type: Optional[str],
    ) -> bool:
        """Route an Other/open-ended reply to its remote A2A interaction."""
        del chat_type
        candidate = None
        for interaction in self._delegate_interactions.values():
            if not interaction.get("awaiting_text") or interaction.get("resolved"):
                continue
            if interaction.get("channel_id") != channel_id or (interaction.get("thread_ts") or None) != (thread_ts or None):
                continue
            owner = str(interaction.get("user_id") or "")
            if owner and owner != str(user_id or ""):
                continue
            candidate = interaction
            break
        if candidate is None:
            return False
        responder = candidate.get("responder")
        interaction_id = str(candidate.get("interaction_id") or "")
        if not interaction_id or not callable(responder):
            return False
        if not responder(interaction_id, "clarify", str(text or "").strip()):
            return False
        candidate["resolved"] = True
        candidate["awaiting_text"] = False
        await self._update_delegate_interaction_message(candidate, f"✅ Clarification sent by {user_id or 'user'}")
        return True

    async def _route_inbound_to_delegate(self, msg_event: Any, *, original_text: str, bot_uid: Optional[str]) -> bool:
        """Capture a follow-up for an active delegate loop before normal dispatch.

        ``/main`` and ``/exit`` are recognised from the original Slack text (thread-context or
        attachment prefixes must not turn them into remote prompts); every other resolvable gateway
        command skips the loop. Cold-start thread backfill is folded into routed text because a
        routed follow-up never reaches the normal ``channel_context`` path.
        """
        if not (getattr(self, "_delegate_routes", None) or getattr(self, "_delegate_interactions", None)):
            return False  # no loop or remote interaction is live on this adapter
        source = msg_event.source
        command_text = (original_text or "").strip()
        if bot_uid:
            command_text = command_text.replace(f"<@{bot_uid}>", "").strip()
        if is_gateway_command_during_delegate_loop(command_text):
            return False
        text = msg_event.text
        routed_text = text
        if command_text in {"/main", "/exit"}:
            routed_text = command_text
        elif msg_event.channel_context:
            routed_text = f"{msg_event.channel_context}{routed_text}"
        route_kwargs = {"channel_id": source.chat_id, "thread_ts": source.thread_id, "user_id": source.user_id,
                        "chat_type": source.chat_type}
        if await self._maybe_route_delegate_interaction_message(text=text, **route_kwargs):
            return True
        return await self._maybe_route_delegate_foreground_message(text=routed_text, **route_kwargs)

    @staticmethod
    def _bot_fallback_sender_name(event: dict, user_id: str) -> Optional[str]:
        """A ``bot_message`` without ``user`` keeps ``bot_id`` as the sender: name it from the event.

        ``bot_id`` is not a user id, so it must never reach users.info. Returns None for ordinary
        user events, which keep the cached users.info path.
        """
        bot_id = str(event.get("bot_id") or "").strip()
        if event.get("user") or not bot_id or user_id != bot_id:
            return None
        profile = event.get("bot_profile")
        profile_name = profile.get("name", "") if isinstance(profile, dict) else ""
        return str(event.get("username") or profile_name or "").strip()
