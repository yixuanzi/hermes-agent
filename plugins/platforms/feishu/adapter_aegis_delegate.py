"""Feishu foreground I/O for the fork's aegis_a2a_delegate loops (Aegis).

Per-route blocking input and adapter-loop output for delegate loops keyed by chat, topic and user;
remote approval/clarify as interactive cards answered through the A2A responder, kept apart from
the local gateway approval/clarify state."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from gateway.platforms.base import (
    SendResult,
)
from plugins.platforms.feishu.adapter_aegis_cards import _CARD_BYPASS_METADATA_KEY

logger = logging.getLogger("plugins.platforms.feishu.adapter")


def _fa():
    """The adapter module, read at call time (no import cycle; SDK names bind at connect)."""
    import sys

    return sys.modules["plugins.platforms.feishu.adapter"]

DEFAULT_DELEGATE_STREAM_EDIT_INTERVAL = 3.0


@dataclass
class _FeishuDelegateRoute:
    key: str
    chat_id: str
    thread_id: Optional[str]
    user_id: Optional[str]
    chat_type: Optional[str]
    input_adapter: Any


@dataclass
class _FeishuDelegateStreamState:
    accumulated_text: str = ""
    pending_text: str = ""
    message_id: Optional[str] = None
    last_rendered_text: str = ""
    lock: Optional[asyncio.Lock] = None
    last_flush_ts: float = 0.0
    flush_task: Optional[asyncio.Task] = None


class _FeishuDelegateInputAdapter:
    """Bridge async Feishu events to the synchronous A2A foreground loop."""

    def __init__(self, adapter: Any, route: _FeishuDelegateRoute):
        self._adapter = adapter
        self._route = route
        self._condition = threading.Condition()
        self._lines: list[str] = []
        self._closed = False
        self._last_read_timed_out = False
        self._a2a_session = None

    def enter_foreground(self) -> bool:
        self._adapter._register_delegate_route(self._route)
        return True

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

    def exit_foreground(self) -> None:
        self._adapter._unregister_delegate_route(self._route)

    def push_line(self, text: str) -> bool:
        with self._condition:
            if self._closed:
                return False
            self._lines.append(str(text or ""))
            self._condition.notify_all()
            return True

    def read_line(self, timeout=None):
        with self._condition:
            self._last_read_timed_out = False
            deadline = None if timeout is None else time.monotonic() + max(0.0, float(timeout))
            while not self._lines and not self._closed:
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._last_read_timed_out = True
                    return None
                self._condition.wait(timeout=remaining)
            if self._lines:
                return self._lines.pop(0)
            return None

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        self.exit_foreground()

    def last_read_timed_out(self) -> bool:
        with self._condition:
            return self._last_read_timed_out


class _FeishuDelegateOutputAdapter:
    """Render A2A delegate events back into the originating Feishu surface."""

    def __init__(
        self,
        adapter: Any,
        *,
        chat_id: str,
        thread_id: Optional[str],
        user_id: Optional[str] = None,
        chat_type: Optional[str] = None,
    ):
        self._adapter = adapter
        self._chat_id = chat_id
        self._thread_id = thread_id
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
            self._emit_async(
                str(source or ""), str(event_type or ""), str(content or ""), session_id=session_id
            )
        )

    def _delegate_agent_name(self) -> Optional[str]:
        """Best-effort remote agent name for the delegate card title."""
        name = getattr(self._a2a_session, "agent_name", None)
        return str(name).strip() or None if name else None

    def _route_key(self) -> str:
        return self._adapter._delegate_route_key(
            chat_id=self._chat_id,
            thread_id=self._thread_id,
            user_id=self._user_id,
            chat_type=self._chat_type,
        )

    @staticmethod
    def _format_tool_call(content: str) -> str:
        tool_name, _sep, raw_args = str(content or "").strip().partition(" ")
        tool_name = tool_name.strip() or "tool"
        preview = " ".join(raw_args.strip().split())
        if len(preview) > 80:
            preview = preview[:77] + "..."
        return f"`tool` {tool_name}: {preview}" if preview else f"`tool` {tool_name}"

    async def _emit_async(self, source: str, event_type: str, content: str, *, session_id=None) -> None:
        metadata = {"thread_id": self._thread_id} if self._thread_id else None
        if source == "delegate" and event_type in {
            "approval_request",
            "clarify_request",
            "approval_resolved",
            "clarify_resolved",
        }:
            try:
                payload = json.loads(content)
            except (TypeError, ValueError):
                payload = {}
            responder = getattr(self._a2a_session, "schedule_interaction_response", None)
            if event_type == "approval_request" and payload:
                await self._adapter.send_delegate_exec_approval(
                    chat_id=self._chat_id,
                    payload=payload,
                    responder=responder if callable(responder) else None,
                    metadata=metadata,
                    user_id=self._user_id,
                )
            elif event_type == "clarify_request" and payload:
                await self._adapter.send_delegate_clarify(
                    chat_id=self._chat_id,
                    payload=payload,
                    responder=responder if callable(responder) else None,
                    metadata=metadata,
                    user_id=self._user_id,
                )
            elif event_type.endswith("_resolved") and payload:
                await self._adapter.resolve_delegate_interaction(payload)
            return

        if source == "delegate" and event_type in {
            "ai_delta", "ai", "tool_call", "status", "error",
        }:
            # Card path: every delegated agent writes into its own card,
            # keyed by A2A context id, so its trace and answer never mix
            # with the main agent's.  Falls through to the legacy
            # message-per-event path when cards are unavailable.
            try:
                handled = await self._adapter.handle_delegate_card_event(
                    chat_id=self._chat_id,
                    metadata=metadata,
                    owner=self._adapter._card_delegate_owner(session_id),
                    agent_name=self._delegate_agent_name(),
                    event_type=event_type,
                    content=content,
                )
            except Exception:
                logger.debug(
                    "[Feishu] delegate card event failed; using messages", exc_info=True
                )
                handled = False
            if handled:
                return

        del session_id
        if source == "delegate" and event_type == "ai_delta":
            if content:
                await self._adapter.handle_delegate_ai_delta(
                    route_key=self._route_key(), chat_id=self._chat_id, content=content, metadata=metadata
                )
            return
        if source == "delegate" and event_type == "ai":
            await self._adapter.handle_delegate_stream_segment_break(
                route_key=self._route_key(), chat_id=self._chat_id, metadata=metadata
            )
            return
        if source == "delegate" and event_type == "tool_call":
            await self._adapter.handle_delegate_stream_segment_break(
                route_key=self._route_key(), chat_id=self._chat_id, metadata=metadata
            )
            await self._adapter.send(self._chat_id, self._format_tool_call(content), metadata=metadata)
            return
        prefix = {"status": "_delegate_", "error": "`delegate error`"}.get(
            event_type, f"`{event_type}`"
        )
        await self._adapter.send(self._chat_id, f"{prefix}: {content}", metadata=metadata)


class FeishuDelegateMixin:
    """Mixed into ``FeishuAdapter`` ahead of ``BasePlatformAdapter``."""

    @staticmethod
    def _trim_oldest_dict_entries(mapping: dict[Any, Any], max_size: int) -> None:
        while len(mapping) > max_size:
            mapping.pop(next(iter(mapping)), None)

    def _delegate_route_key(
        self, *, chat_id: str, thread_id: Optional[str], user_id: Optional[str], chat_type: Optional[str]
    ) -> str:
        """Use the normal Hermes session identity for foreground route isolation."""
        try:
            from gateway.session import build_session_key

            return build_session_key(
                self.build_source(
                    chat_id=chat_id,
                    chat_type=chat_type or "dm",
                    user_id=user_id,
                    thread_id=thread_id,
                ),
                # A foreground loop owns a blocking input queue.  Unlike the
                # regular group-thread transcript (which may be intentionally
                # shared), it must never let another participant feed that
                # queue.  Keep both group and threaded routes per-user.
                group_sessions_per_user=True,
                thread_sessions_per_user=True,
            )
        except Exception:
            return json.dumps(
                {"platform": "feishu", "chat_id": chat_id, "thread_id": thread_id,
                 "user_id": user_id, "chat_type": chat_type},
                sort_keys=True,
            )

    def _register_delegate_route(self, route: _FeishuDelegateRoute) -> None:
        with self._delegate_routes_lock:
            self._delegate_routes[route.key] = route

    def _unregister_delegate_route(self, route: _FeishuDelegateRoute) -> None:
        with self._delegate_routes_lock:
            if self._delegate_routes.get(route.key) is route:
                self._delegate_routes.pop(route.key, None)
        self._clear_delegate_stream_state(route.key)

    def _get_delegate_route(
        self, *, chat_id: str, thread_id: Optional[str], user_id: Optional[str], chat_type: Optional[str]
    ) -> Optional[_FeishuDelegateRoute]:
        key = self._delegate_route_key(
            chat_id=chat_id, thread_id=thread_id, user_id=user_id, chat_type=chat_type
        )
        with self._delegate_routes_lock:
            return self._delegate_routes.get(key)

    def _schedule_delegate_output(self, coro) -> None:
        loop = self._delegate_loop or self._loop
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is loop:
            running_loop.create_task(coro)
        elif loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, loop)
        else:
            logger.warning("[Feishu] Dropping delegate output because adapter loop is unavailable")
            coro.close()

    def _delegate_stream_edit_interval(self) -> float:
        try:
            return max(0.0, float(getattr(
                self, "_delegate_stream_edit_interval", DEFAULT_DELEGATE_STREAM_EDIT_INTERVAL
            )))
        except (TypeError, ValueError):
            return DEFAULT_DELEGATE_STREAM_EDIT_INTERVAL

    def _split_delegate_stream_content(self, text: str) -> list[str]:
        return self.truncate_message(str(text or ""), max(1, self.MAX_MESSAGE_LENGTH - 64)) if text else []

    def _get_delegate_stream_state(self, route_key: str) -> _FeishuDelegateStreamState:
        with self._delegate_routes_lock:
            state = self._delegate_stream_states.get(route_key)
            if state is None:
                state = _FeishuDelegateStreamState()
                self._delegate_stream_states[route_key] = state
        if state.lock is None:
            state.lock = asyncio.Lock()
        return state

    @staticmethod
    def _reset_delegate_stream_segment_locked(state: _FeishuDelegateStreamState) -> None:
        if state.flush_task is not None and not state.flush_task.done():
            state.flush_task.cancel()
        state.accumulated_text = ""
        state.pending_text = ""
        state.message_id = None
        state.last_rendered_text = ""
        state.last_flush_ts = 0.0
        state.flush_task = None

    def _clear_delegate_stream_state(self, route_key: str) -> None:
        with self._delegate_routes_lock:
            state = self._delegate_stream_states.pop(route_key, None)
        if state is not None and state.flush_task is not None and not state.flush_task.done():
            state.flush_task.cancel()

    async def _flush_delegate_stream_after_delay(
        self, *, route_key: str, chat_id: str, metadata: Optional[dict[str, Any]], delay: float
    ) -> None:
        try:
            await asyncio.sleep(max(0.0, delay))
            state = self._delegate_stream_states.get(route_key)
            if state is None or state.lock is None:
                return
            async with state.lock:
                if state.flush_task is not asyncio.current_task():
                    return
                state.flush_task = None
                await self._flush_delegate_stream_locked(
                    state=state, chat_id=chat_id, metadata=metadata, route_key=route_key, force=True
                )
        except asyncio.CancelledError:
            return

    def _defer_delegate_flush(self, state, *, chat_id, metadata, route_key) -> bool:
        """Throttle edits of the same message: schedule one delayed flush and report True."""
        interval = self._delegate_stream_edit_interval()
        elapsed = time.monotonic() - state.last_flush_ts
        if elapsed >= interval:
            return False
        if route_key and (state.flush_task is None or state.flush_task.done()):
            state.flush_task = asyncio.create_task(self._flush_delegate_stream_after_delay(
                route_key=route_key, chat_id=chat_id, metadata=metadata, delay=interval - elapsed,
            ))
        return True

    async def _send_delegate_groups(self, state, groups, *, chat_id, metadata) -> bool:
        """Send each group as a new message, tracking the last id; False on the first failure."""
        for group in groups:
            result = await self.send(chat_id, group, metadata=metadata)
            if not result.success:
                return False
            if result.message_id:
                state.message_id = str(result.message_id)
        return True

    async def _edit_or_resend(self, state, text, fallback_text, *, chat_id, metadata) -> bool:
        """Edit the streaming message; after an edit failure append ``fallback_text`` instead."""
        result = await self.edit_message(chat_id, state.message_id, text)
        if result.success:
            return True
        return await self._send_delegate_groups(state, [fallback_text], chat_id=chat_id, metadata=metadata)

    async def _flush_delegate_stream_locked(
        self,
        *,
        state: _FeishuDelegateStreamState,
        chat_id: str,
        metadata: Optional[dict[str, Any]],
        route_key: Optional[str] = None,
        force: bool = False,
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
            ok = await self._send_delegate_groups(state, groups, chat_id=chat_id, metadata=metadata)
        elif len(groups) == 1:
            ok = groups[0] == state.last_rendered_text or await self._edit_or_resend(
                state, groups[0], state.pending_text, chat_id=chat_id, metadata=metadata)
        else:
            # Past the single-message limit: finish the current message, send the rest separately.
            ok = (await self._edit_or_resend(state, groups[0], groups[0], chat_id=chat_id, metadata=metadata)
                  and await self._send_delegate_groups(state, groups[1:], chat_id=chat_id, metadata=metadata))
        if not ok:
            return
        state.accumulated_text = groups[-1]
        state.last_rendered_text = groups[-1]
        state.pending_text = ""
        state.last_flush_ts = time.monotonic()

    async def handle_delegate_ai_delta(
        self, *, route_key: str, chat_id: str, content: str, metadata: Optional[dict[str, Any]]
    ) -> None:
        state = self._get_delegate_stream_state(route_key)
        assert state.lock is not None
        async with state.lock:
            state.accumulated_text += str(content or "")
            state.pending_text += str(content or "")
            await self._flush_delegate_stream_locked(
                state=state, chat_id=chat_id, metadata=metadata, route_key=route_key
            )

    async def handle_delegate_stream_segment_break(
        self, *, route_key: str, chat_id: str, metadata: Optional[dict[str, Any]]
    ) -> None:
        state = self._delegate_stream_states.get(route_key)
        if state is None or state.lock is None:
            return
        async with state.lock:
            await self._flush_delegate_stream_locked(
                state=state, chat_id=chat_id, metadata=metadata, route_key=route_key, force=True
            )
            self._reset_delegate_stream_segment_locked(state)

    def build_delegate_foreground_runtime(
        self,
        *,
        channel_id: str,
        thread_ts: Optional[str],
        user_id: Optional[str] = None,
        chat_type: Optional[str] = None,
    ) -> dict[str, Any]:
        try:
            self._delegate_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        route_key = self._delegate_route_key(
            chat_id=channel_id, thread_id=thread_ts, user_id=user_id, chat_type=chat_type
        )

        def _input_factory():
            route = _FeishuDelegateRoute(
                key=route_key, chat_id=channel_id, thread_id=thread_ts, user_id=user_id,
                chat_type=chat_type, input_adapter=None,
            )
            input_adapter = _FeishuDelegateInputAdapter(self, route)
            route.input_adapter = input_adapter
            return input_adapter

        return {
            "output": _FeishuDelegateOutputAdapter(
                self, chat_id=channel_id, thread_id=thread_ts, user_id=user_id, chat_type=chat_type
            ),
            "input_factory": _input_factory,
            "metadata": {"thread_id": thread_ts} if thread_ts else None,
        }

    async def _maybe_route_delegate_foreground_message(
        self,
        *,
        text: str,
        chat_id: str,
        thread_id: Optional[str],
        user_id: Optional[str],
        chat_type: Optional[str],
    ) -> bool:
        # Some lightweight embedders construct an adapter shell only for
        # normal message parsing and bypass ``__init__``.  Without foreground
        # runtime state there cannot be a route to claim, so preserve the
        # historical pass-through behaviour.
        if not hasattr(self, "_delegate_routes_lock"):
            return False
        route = self._get_delegate_route(
            chat_id=chat_id, thread_id=thread_id, user_id=user_id, chat_type=chat_type
        )
        if route is None or not callable(getattr(route.input_adapter, "push_line", None)):
            return False
        metadata = {"thread_id": thread_id} if thread_id else None
        await self.handle_delegate_stream_segment_break(
            route_key=route.key, chat_id=chat_id, metadata=metadata,
        )
        # The steer acknowledgement is a notice, not the delegate's answer: as a card body it would
        # change the speaker and seal the delegate card its answer is still streaming into.
        ack_metadata = {**(metadata or {}), _CARD_BYPASS_METADATA_KEY: True}
        if await self._steer_delegate_turn(route.input_adapter, text, chat_id=chat_id, metadata=ack_metadata):
            return True
        if route.input_adapter.push_line(text):
            return True
        self._unregister_delegate_route(route)
        return False

    async def _maybe_route_delegate_interaction_message(
        self,
        *,
        text: str,
        chat_id: str,
        thread_id: Optional[str],
        user_id: Optional[str],
        chat_type: Optional[str],
    ) -> bool:
        del chat_type
        # Same lightweight-embedder tolerance as the foreground router: an
        # adapter shell built without ``__init__`` has no pending remote
        # interactions, so there is nothing for this text to answer.
        interactions = getattr(self, "_delegate_interactions", None)
        if not interactions:
            return False
        candidate = None
        for state in interactions.values():
            if state.get("kind") != "clarify" or not state.get("awaiting_text") or state.get("resolved"):
                continue
            if str(state.get("chat_id") or "") != str(chat_id or ""):
                continue
            if (state.get("thread_id") or None) != (thread_id or None):
                continue
            owner = str(state.get("user_id") or "")
            if owner and owner != str(user_id or ""):
                continue
            candidate = state
            break
        if candidate is None:
            return False
        responder = candidate.get("responder")
        interaction_id = str(candidate.get("interaction_id") or "")
        if not interaction_id or not callable(responder) or not responder(interaction_id, "clarify", str(text or "").strip()):
            return False
        candidate["resolved"] = True
        candidate["awaiting_text"] = False
        await self._send_delegate_resolution(candidate, "✅ Clarification response sent.")
        return True

    def _close_delegate_routes(self) -> None:
        """Wake blocked foreground readers and discard all adapter-local state."""
        with self._delegate_routes_lock:
            routes = list(self._delegate_routes.values())
            self._delegate_routes.clear()
            states = list(self._delegate_stream_states.values())
            self._delegate_stream_states.clear()
        for route in routes:
            close = getattr(route.input_adapter, "close", None)
            if callable(close):
                close()
        for state in states:
            if state.flush_task is not None and not state.flush_task.done():
                state.flush_task.cancel()

    async def send_delegate_exec_approval(
        self,
        *,
        chat_id: str,
        payload: dict[str, Any],
        responder,
        metadata: Optional[dict[str, Any]] = None,
        user_id: Optional[str] = None,
    ) -> SendResult:
        interaction_id = str(payload.get("interaction_id") or "")
        if not interaction_id or not self._client:
            return SendResult(success=False, error="Missing A2A interaction id or client")
        try:
            prefix = {"hermes_delegate_kind": "approval", "interaction_id": interaction_id}
            def _btn(label: str, choice: str, button_type: str = "default") -> dict:
                return {"tag": "button", "text": {"tag": "plain_text", "content": label}, "type": button_type, "value": {**prefix, "choice": choice}}
            actions = [_btn("✅ Allow Once", "once", "primary")]
            if payload.get("allow_session", True):
                actions.append(_btn("✅ Session", "session"))
            if payload.get("allow_permanent", True):
                actions.append(_btn("✅ Always", "always"))
            actions.append(_btn("❌ Deny", "deny", "danger"))
            card = {
                "config": {"wide_screen_mode": True},
                "header": {"title": {"content": "⚠️ Delegate Approval Required", "tag": "plain_text"}, "template": "orange"},
                "elements": [
                    {"tag": "markdown", "content": self._format_exec_approval(str(payload.get("command") or ""), str(payload.get("description") or "dangerous command"), False)},
                    {"tag": "action", "actions": actions},
                ],
            }
            response = await self._feishu_send_with_retry(
                chat_id=chat_id,
                msg_type="interactive",
                payload=json.dumps(card, ensure_ascii=False),
                # Let _send_raw_message pick the target from metadata (an om_* anchor,
                # or a real omt_* topic's root via _topic_reply_anchor). Passing omt_*
                # as reply_to hits the reply API with a non-message id and is lost.
                reply_to=None,
                metadata=metadata,
            )
            result = self._finalize_send_result(response, "send_delegate_exec_approval failed")
            if not result.success:
                logger.warning(
                    "[Feishu] delegate approval card not delivered: interaction=%s chat=%s thread=%s error=%s",
                    interaction_id, chat_id, (metadata or {}).get("thread_id"), result.error,
                )
            if result.success:
                self._delegate_interactions[interaction_id] = {
                    "kind": "approval", "interaction_id": interaction_id, "responder": responder,
                    "chat_id": chat_id, "thread_id": (metadata or {}).get("thread_id"), "user_id": user_id,
                    "message_id": result.message_id or "", "command": str(payload.get("command") or ""), "resolved": False,
                }
                self._trim_oldest_dict_entries(self._delegate_interactions, self._DELEGATE_INTERACTIONS_MAX)
            return result
        except Exception as exc:
            logger.warning("[Feishu] send_delegate_exec_approval failed: %s", exc, exc_info=True)
            return SendResult(success=False, error=str(exc))

    async def send_delegate_clarify(
        self,
        *,
        chat_id: str,
        payload: dict[str, Any],
        responder,
        metadata: Optional[dict[str, Any]] = None,
        user_id: Optional[str] = None,
    ) -> SendResult:
        interaction_id = str(payload.get("interaction_id") or "")
        if not interaction_id or not self._client:
            return SendResult(success=False, error="Missing A2A interaction id or client")
        choices = [str(item) for item in (payload.get("choices") or []) if str(item).strip()]
        state = {
            "kind": "clarify", "interaction_id": interaction_id, "responder": responder,
            "chat_id": chat_id, "thread_id": (metadata or {}).get("thread_id"), "user_id": user_id,
            "question": str(payload.get("question") or "Clarification required"), "choices": choices,
            "multi_select": bool(payload.get("multi_select")), "selected": [],
            "awaiting_text": not bool(choices), "resolved": False,
        }
        try:
            if not choices:
                card = {
                    "config": {"wide_screen_mode": True},
                    "header": {"title": {"content": "❓ Delegate Clarification", "tag": "plain_text"}, "template": "blue"},
                    "elements": [{"tag": "markdown", "content": state["question"][:3000]}],
                }
            else:
                base = {"hermes_delegate_kind": "clarify", "interaction_id": interaction_id}
                actions = [
                    {"tag": "button", "text": {"tag": "plain_text", "content": choice[:200]}, "type": "primary" if index == 0 else "default", "value": {**base, "token": str(index)}}
                    for index, choice in enumerate(choices)
                ]
                actions.append({"tag": "button", "text": {"tag": "plain_text", "content": "Other (type answer)"}, "type": "default", "value": {**base, "token": "other"}})
                if state["multi_select"]:
                    actions.append({"tag": "button", "text": {"tag": "plain_text", "content": "Submit selection"}, "type": "primary", "value": {**base, "token": "submit"}})
                card = {
                    "config": {"wide_screen_mode": True},
                    "header": {"title": {"content": "❓ Delegate Clarification", "tag": "plain_text"}, "template": "blue"},
                    "elements": [{"tag": "markdown", "content": state["question"][:3000]}, {"tag": "action", "actions": actions}],
                }
            response = await self._feishu_send_with_retry(
                chat_id=chat_id, msg_type="interactive", payload=json.dumps(card, ensure_ascii=False),
                reply_to=None, metadata=metadata,  # target resolved from metadata thread_id
            )
            result = self._finalize_send_result(response, "send_delegate_clarify failed")
            if not result.success:
                logger.warning(
                    "[Feishu] delegate clarify card not delivered: interaction=%s chat=%s thread=%s error=%s",
                    interaction_id, chat_id, state.get("thread_id"), result.error,
                )
            if result.success:
                state["message_id"] = result.message_id or ""
                self._delegate_interactions[interaction_id] = state
                self._trim_oldest_dict_entries(self._delegate_interactions, self._DELEGATE_INTERACTIONS_MAX)
            return result
        except Exception as exc:
            logger.warning("[Feishu] send_delegate_clarify failed: %s", exc, exc_info=True)
            return SendResult(success=False, error=str(exc))

    async def resolve_delegate_interaction(self, payload: dict[str, Any]) -> None:
        interaction_id = str(payload.get("interaction_id") or "")
        state = self._delegate_interactions.get(interaction_id)
        if state is None:
            return
        state["resolved"] = True
        state["awaiting_text"] = False
        await self._send_delegate_resolution(state, "✅ Delegate interaction resolved.")

    def _handle_delegate_card_action(self, *, event: Any, action_value: dict[str, Any], loop: Any) -> Any:
        interaction_id = str(action_value.get("interaction_id") or "")
        state = self._delegate_interactions.get(interaction_id)
        if not interaction_id or state is None or state.get("resolved"):
            return self._empty_card_action_response()
        callback_chat_id = str(getattr(getattr(event, "context", None), "open_chat_id", "") or "")
        if callback_chat_id and callback_chat_id != str(state.get("chat_id") or ""):
            return self._empty_card_action_response()
        operator = getattr(event, "operator", None)
        open_id = str(getattr(operator, "open_id", "") or "")
        operator_user_id = str(getattr(operator, "user_id", "") or "")
        # ``SessionSource.user_id`` is tenant-scoped when Feishu provides it,
        # while card callbacks identify the operator with both the
        # app-scoped ``open_id`` and (when available) the tenant-scoped
        # ``user_id``.  Accept either representation here; comparing the
        # stored source id only to ``open_id`` rejects legitimate delegate
        # clicks before the A2A responder can run.
        stored_user_id = str(state.get("user_id") or "")
        if stored_user_id and stored_user_id not in {open_id, operator_user_id}:
            logger.warning(
                "[Feishu] Delegate callback user mismatch for interaction %s",
                interaction_id,
            )
            return self._empty_card_action_response()
        sender_id = SimpleNamespace(open_id=open_id, user_id=operator_user_id)
        if not self._allow_group_message(sender_id, str(state.get("chat_id") or ""), is_bot=False) or not self._is_interactive_operator_authorized(open_id):
            return self._empty_card_action_response()

        token = str(action_value.get("token") or action_value.get("choice") or "")
        if state.get("kind") == "approval":
            if token not in {"once", "session", "always", "deny"}:
                return self._empty_card_action_response()
            responder = state.get("responder")
            if not callable(responder) or not responder(interaction_id, "approval", token):
                return self._delegate_card_response("Delegate approval", "The remote response channel is unavailable.")
            state["resolved"] = True
            return self._delegate_card_response("Delegate approval resolved", f"{token} by {open_id}")

        if token == "other":
            state["awaiting_text"] = True
            return self._delegate_card_response("Delegate clarification", f"Waiting for a typed answer from {open_id}.")
        if token == "submit":
            selected = list(state.get("selected") or [])
            if not selected:
                return self._delegate_card_response("Delegate clarification", "Select at least one option first.")
            value_to_send: Any = selected
        else:
            try:
                choice = list(state.get("choices") or [])[int(token)]
            except (ValueError, TypeError, IndexError):
                return self._empty_card_action_response()
            if state.get("multi_select"):
                selected = list(state.get("selected") or [])
                if choice in selected:
                    selected.remove(choice)
                else:
                    selected.append(choice)
                state["selected"] = selected
                return self._delegate_card_response("Delegate clarification", "Selected: " + (", ".join(selected) if selected else "none"))
            value_to_send = choice
        responder = state.get("responder")
        if not callable(responder) or not responder(interaction_id, "clarify", value_to_send):
            return self._delegate_card_response("Delegate clarification", "The remote response channel is unavailable.")
        state["resolved"] = True
        state["awaiting_text"] = False
        return self._delegate_card_response("Delegate clarification resolved", f"Response sent by {open_id}.")

    def _delegate_card_response(self, title: str, content: str) -> Any:
        return self._build_card_action_response({
            "config": {"wide_screen_mode": True},
            "header": {"title": {"content": title, "tag": "plain_text"}, "template": "blue"},
            "elements": [{"tag": "markdown", "content": content[:3000]}],
        })

    async def _send_delegate_resolution(self, state: dict[str, Any], text: str) -> None:
        # "✅ Delegate interaction resolved." / "✅ Clarification response
        # sent." acknowledge a button press on an interaction card.  They are
        # not the delegate's answer, so they must not be appended to the card
        # that answer is streaming into.
        metadata: dict[str, Any] = {_CARD_BYPASS_METADATA_KEY: True}
        if state.get("thread_id"):
            metadata["thread_id"] = state.get("thread_id")
        try:
            await self.send(
                str(state.get("chat_id") or ""),
                text,
                metadata=metadata,
            )
        except Exception:
            logger.debug("[Feishu] delegate resolution notice failed", exc_info=True)

    def _empty_card_action_response(self) -> Any:
        return _fa().P2CardActionTriggerResponse() if _fa().P2CardActionTriggerResponse else None

    @staticmethod
    def _build_card_action_response(card_data: dict[str, Any]) -> Any:
        if _fa().P2CardActionTriggerResponse is None:
            return None
        response = _fa().P2CardActionTriggerResponse()
        if _fa().CallBackCard is not None:
            card = _fa().CallBackCard()
            card.type = "raw"
            card.data = card_data
            response.card = card
        return response
