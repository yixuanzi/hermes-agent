"""Session manager + actor for AISOC chat WebSocket flows.

Forked from ``aegis/backend/chat/service.py`` (2026-08). AISOC now has its own
user account system (see ``aisoc/backend/auth.py``); the WebSocket route binds
each session with the authenticated user's ``user_id``, which isolates the
same public session id per user and derives a distinct persisted
``hermes_state.SessionDB`` runtime id (see ``_runtime_session_id``). When
``user_id`` is empty (e.g. anonymous/legacy callers), the public and runtime
session ids remain identical.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import hashlib
import inspect
import json
import os
import threading
import time
from typing import Any
from uuid import uuid4

from fastapi import WebSocket

from aisoc.backend.chat.models import (
    ApprovalRequestState,
    ChatEventEnvelope,
    ClarifyRequestState,
    DelegateForegroundState,
)
from aisoc.backend.chat.attachments import ChatAttachment, ChatAttachmentStore, prepare_turn_message
from aisoc.backend.chat.runtime import ChatInputAdapter, ChatOutputAdapter
from aisoc.backend.agent_runtime import default_agent_factory
from aisoc.backend.agent_runtime import load_conversation_history
from gateway.session_context import clear_session_vars, set_session_vars
from agent.tool_dispatch_helpers import _extract_landed_file_mutation_paths
from tools.approval import (
    register_gateway_notify,
    reset_current_session_key,
    resolve_gateway_approval,
    set_current_session_key,
    unregister_gateway_notify,
)


CHAT_PLATFORM = "aisoc_web"


def _now_timestamp() -> float:
    return time.time()


def _conversation_title(seed: str | None, fallback: str = "New Conversation") -> str:
    text = str(seed or "").strip()
    if not text:
        return fallback
    return text[:30] + ("..." if len(text) > 30 else "")


def _runtime_session_id(session_id: str, user_id: str | None) -> str:
    """Keep public session ids stable while isolating persisted runtime state."""
    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        return session_id
    digest = hashlib.sha256(
        f"{normalized_user_id}\0{session_id}".encode("utf-8")
    ).hexdigest()[:32]
    return f"aisoc-{digest}"


def _message_delta_enabled() -> bool:
    value = str(os.getenv("MESSAGE_DELTA", "true")).strip().lower()
    return value not in {"0", "false", "no", "off"}


def _clarify_timeout_seconds() -> float:
    raw = str(os.getenv("AISOC_CLARIFY_TIMEOUT_SECONDS", "600")).strip()
    try:
        timeout = float(raw)
    except ValueError:
        return 600.0
    return timeout if timeout > 0 else 600.0


def _successful_mutation_paths(
    tool_name: str,
    function_args: dict | None,
    function_result: object,
) -> list[str]:
    """Return every successful file edit reported by one main-task file tool."""
    if tool_name not in {"write_file", "patch"}:
        return []
    if isinstance(function_result, str):
        try:
            result_data = json.loads(function_result)
        except (TypeError, ValueError):
            result_data = None
        if isinstance(result_data, dict) and result_data.get("error"):
            return []
    candidates = _extract_landed_file_mutation_paths(
        tool_name,
        function_args or {},
        function_result,
    )
    paths: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        reported_path = str(candidate).strip()
        if reported_path and reported_path not in seen:
            seen.add(reported_path)
            paths.append(reported_path)
    return paths


def build_aisoc_ephemeral_system_prompt() -> str | None:
    from tools import a2a_delegate_tool

    cached_context = str(getattr(a2a_delegate_tool, "A2A_CONTEXT", "") or "").strip()
    if cached_context:
        return cached_context

    try:
        a2a_delegate_tool.a2a_list()
    except Exception:
        return None

    refreshed_context = str(getattr(a2a_delegate_tool, "A2A_CONTEXT", "") or "").strip()
    return refreshed_context or None


def _aisoc_help_text() -> str:
    return (
        "AISOC slash commands:\n"
        "/help - show available AISOC-native slash commands\n"
        "/model <model_name> - switch the current live session model\n"
        "/a2a - show the current A2A context XML\n"
        "/stop - cancel the active remote A2A delegate task"
    )


def _refresh_live_agent_runtime_after_model_switch(agent: object) -> None:
    client_kwargs = getattr(agent, "_client_kwargs", None)
    if not isinstance(client_kwargs, dict):
        return

    base_url = str(getattr(agent, "base_url", "") or "")
    refresh_headers = getattr(agent, "_apply_client_headers_for_base_url", None)
    if callable(refresh_headers) and base_url:
        refresh_headers(base_url)

    api_mode = str(getattr(agent, "api_mode", "") or "")
    rebuild_client = getattr(agent, "_create_openai_client", None)
    if callable(rebuild_client) and api_mode not in {"anthropic_messages", "bedrock_converse"}:
        agent.client = rebuild_client(
            dict(client_kwargs),
            reason="aisoc_slash_model_switch",
            shared=True,
        )

    primary_runtime = getattr(agent, "_primary_runtime", None)
    if isinstance(primary_runtime, dict):
        primary_runtime.update(
            {
                "model": getattr(agent, "model", ""),
                "provider": getattr(agent, "provider", ""),
                "base_url": getattr(agent, "base_url", ""),
                "api_mode": getattr(agent, "api_mode", ""),
                "api_key": getattr(agent, "api_key", ""),
                "client_kwargs": dict(client_kwargs),
            }
        )


def _build_default_aisoc_agent(
    session_id: str,
    *,
    user_id: str | None = None,
    user_name: str | None = None,
) -> object:
    kwargs: dict[str, object] = {
        "platform": CHAT_PLATFORM,
        "ephemeral_system_prompt": build_aisoc_ephemeral_system_prompt(),
        # AISOC web chat sessions are meant to start blank: skip both the
        # built-in MEMORY.md/USER.md injection and any external memory
        # provider (Hindsight, etc.) so a brand-new "新建会话" never inherits
        # context accumulated from other sessions/platforms. Other AISOC
        # The server chat path calls default_agent_factory() directly.
        # and are unaffected.
        "skip_memory": True,
        # skip_memory only turns off the *automatic* MEMORY.md/Hindsight
        # injection at session start. `session_search` is a separate,
        # always-registered tool (part of the "hermes-cli" toolset bundle)
        # that lets the model itself pull real transcript content from OTHER
        # sessions mid-conversation on its own initiative — the system prompt
        # (agent/prompt_builder.py) actively nudges it to do so. That's what
        # was surfacing other sessions' content well into an AISOC chat, not
        # just at creation. Strip it specifically for this platform.
        "disabled_toolsets": ["session_search"],
    }
    if user_id:
        kwargs["user_id"] = user_id
    if user_name:
        kwargs["user_name"] = user_name
    return default_agent_factory(
        session_id,
        **kwargs,
    )


def _invoke_agent_factory(
    agent_factory: Callable[..., object],
    session_id: str,
    *,
    user_id: str | None = None,
    user_name: str | None = None,
) -> object:
    kwargs: dict[str, str] = {}
    try:
        parameters = inspect.signature(agent_factory).parameters
    except (TypeError, ValueError):
        parameters = {}
    accepts_var_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if user_id and (accepts_var_kwargs or "user_id" in parameters):
        kwargs["user_id"] = str(user_id)
    if user_name and (accepts_var_kwargs or "user_name" in parameters):
        kwargs["user_name"] = str(user_name)
    return agent_factory(session_id, **kwargs)


class ChatSessionActor:
    def __init__(
        self,
        *,
        session_id: str,
        title: str,
        agent_factory: Callable[..., object],
        user_id: str | None = None,
        user_name: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.title = title
        self._agent_factory = agent_factory
        self._user_id = str(user_id or "").strip()
        self._user_name = str(user_name or "").strip()
        self._runtime_session_id = _runtime_session_id(session_id, self._user_id)
        self._agent = _invoke_agent_factory(
            agent_factory,
            self._runtime_session_id,
            user_id=self._user_id or None,
            user_name=self._user_name or None,
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._websocket: WebSocket | None = None
        self._lock = threading.RLock()
        # Browser workflow traces outlive this in-memory actor.  Keep a new
        # actor's event ids distinct when the same public session is rebound
        # after an AISOC service restart.
        self._event_epoch = uuid4().hex
        self._event_counter = 0
        self._turn_id: str | None = None
        self._foreground_source = "main"
        self._foreground_agent = ""
        self._foreground_state = DelegateForegroundState()
        self._delegate_input: ChatInputAdapter | None = None
        self._pending_delegate_agent_name = ""
        self._pending_approval: ApprovalRequestState | None = None
        self._pending_clarify: ClarifyRequestState | None = None
        self._last_run_state = "idle"
        self._last_state_source = "main"
        self._latest_delegate_message_id: str | None = None
        self._main_message_id: str | None = None
        self._running_thread: threading.Thread | None = None
        self._disconnect_requested = False
        self._output_adapter = ChatOutputAdapter(self)

    def _heal_session_owner(self) -> None:
        """Best-effort: stamp the persisted session row with its owning account.

        ``run_agent.py``'s ``_ensure_db_session`` always creates the row with
        ``user_id=None`` (it has no notion of AISOC's account system), and the
        row is created lazily on first turn — so this can't run once at bind
        time, it must self-heal after every turn. No-ops (and never raises)
        when the actor is unowned or the underlying session DB is unavailable.
        """
        if not self._user_id:
            return
        session_db = getattr(self._agent, "_session_db", None)
        if session_db is None:
            return
        try:
            session_db.set_session_user_id(self._runtime_session_id, self._user_id)
        except Exception:
            pass

    def update_identity(self, *, user_id: str | None, user_name: str | None) -> None:
        """Refresh mutable account metadata on a cached, user-scoped actor."""
        normalized_user_id = str(user_id or "").strip()
        if normalized_user_id and normalized_user_id != self._user_id:
            raise ValueError("Cannot change the owner of a cached AISOC session")
        self._user_name = str(user_name or "").strip()
        if self._user_id:
            setattr(self._agent, "_user_id", self._user_id)
            setattr(self._agent, "_user_name", self._user_name)

    def replace_connection(self, websocket: WebSocket, loop: asyncio.AbstractEventLoop) -> None:
        with self._lock:
            self._websocket = websocket
            self._loop = loop
            self._disconnect_requested = False

    def detach_connection(self, websocket: WebSocket) -> None:
        with self._lock:
            if self._websocket is websocket:
                self._websocket = None
                self._loop = None
                self._disconnect_requested = True

    def build_bound_event(self, *, resumed: bool) -> dict[str, Any]:
        return self._make_event(
            "session.bound",
            payload={
                "title": self.title,
                "resumed": resumed,
            },
            source=self._foreground_source,
            turn_id=self._turn_id,
        )

    def resume_state_events(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = [
            self._make_event(
                "run.state",
                payload={
                    "state": self._last_run_state,
                    "srcagent": self._foreground_agent or None,
                },
                source=self._last_state_source,
                turn_id=self._turn_id,
            )
        ]
        with self._lock:
            if self._foreground_source == "delegate" and self._foreground_agent:
                events.append(
                    self._make_event(
                        "delegate.entered",
                        payload={
                            "child_session_id": self._foreground_state.child_session_id,
                            "srcagent": self._foreground_agent,
                        },
                        source="delegate",
                        turn_id=self._turn_id,
                    )
                )
            if self._pending_approval is not None:
                events.append(
                    self._make_event(
                        "approval.request",
                        payload={
                            "approval_id": self._pending_approval.approval_id,
                            "command": self._pending_approval.command,
                            "description": self._pending_approval.description,
                            "choices": list(self._pending_approval.choices),
                        },
                        source=self._foreground_source,
                        turn_id=self._turn_id,
                    )
                )
            if self._pending_clarify is not None:
                events.append(
                    self._make_event(
                        "clarify.request",
                        payload={
                            "clarify_id": self._pending_clarify.clarify_id,
                            "question": self._pending_clarify.question,
                            "choices": list(self._pending_clarify.choices or []) or None,
                        },
                        source=self._foreground_source,
                        turn_id=self._turn_id,
                    )
                )
        return events

    def set_title(self, title: str | None) -> None:
        if title:
            self.title = _conversation_title(title, fallback=self.title)

    @staticmethod
    def _native_slash_parts(text: str) -> tuple[str, str] | None:
        stripped = str(text or "").strip()
        if not stripped.startswith("/"):
            return None
        first_word, _, remainder = stripped.partition(" ")
        command = first_word.lower()
        if command not in {"/a2a", "/help", "/model", "/stop"}:
            return None
        return command, remainder.strip()

    def _emit_main_text_reply(self, content: str, turn_id: str) -> None:
        self._send_event(
            "message.completed",
            {
                "message_id": f"assistant_{uuid4().hex[:10]}",
                "content": str(content or ""),
                "completed": True,
            },
            source="main",
            turn_id=turn_id,
        )

    def _handle_a2a_slash(self, turn_id: str) -> None:
        from tools import a2a_delegate_tool

        cached_context = str(getattr(a2a_delegate_tool, "A2A_CONTEXT", "") or "").strip()
        if cached_context:
            self._emit_main_text_reply(cached_context, turn_id)
            return

        try:
            a2a_delegate_tool.a2a_list()
        except Exception as exc:
            self._emit_main_text_reply(f"A2A context refresh failed: {exc}", turn_id)
            return

        refreshed_context = str(getattr(a2a_delegate_tool, "A2A_CONTEXT", "") or "").strip()
        self._emit_main_text_reply(refreshed_context or "A2A context is empty.", turn_id)

    def _handle_help_slash(self, turn_id: str) -> None:
        self._emit_main_text_reply(_aisoc_help_text(), turn_id)

    def _active_remote_a2a_cancel_handle(self):
        handle = getattr(self._agent, "_active_a2a_delegate_session", None)
        if handle is None:
            return None
        has_live_task = getattr(handle, "has_live_task", None)
        if callable(has_live_task):
            try:
                return handle if has_live_task() else None
            except Exception:
                return None
        return handle if callable(getattr(handle, "cancel", None)) else None

    @staticmethod
    def _is_cancel_request_sent(cancel_status: object) -> bool:
        return cancel_status in {"sent", "completed", True}

    @staticmethod
    def _is_cancel_noop(cancel_status: object) -> bool:
        return cancel_status in {"noop", False, None}

    def _cancel_active_remote_a2a_delegate(self, *, turn_id: str, source: str) -> bool:
        handle = self._active_remote_a2a_cancel_handle()
        if handle is None:
            return False
        cancel = getattr(handle, "cancel", None)
        if not callable(cancel):
            return False
        try:
            cancel_status = cancel()
        except Exception as exc:
            self._send_event(
                "error",
                {
                    "code": "delegate_cancel_failed",
                    "message": f"Failed to cancel remote A2A task: {exc}",
                    "srcagent": self._foreground_agent or None,
                },
                source="delegate",
                turn_id=turn_id,
            )
            return True
        if self._is_cancel_noop(cancel_status):
            return False
        if self._is_cancel_request_sent(cancel_status):
            self._set_run_state("interrupted", source=source)
            return True
        self._send_event(
            "error",
            {
                "code": "delegate_cancel_failed",
                "message": f"Failed to cancel remote A2A task: unexpected status {cancel_status!r}",
                "srcagent": self._foreground_agent or None,
            },
            source="delegate",
            turn_id=turn_id,
        )
        return True

    def _handle_stop_slash(self, turn_id: str) -> bool:
        if self._cancel_active_remote_a2a_delegate(turn_id=turn_id, source="delegate"):
            return True
        self._emit_main_text_reply("No active remote A2A delegate task.", turn_id)
        return True

    def _handle_model_slash(self, arg: str, turn_id: str) -> None:
        model_input = str(arg or "").strip()
        if not model_input:
            self._emit_main_text_reply("Usage: /model <model_name>", turn_id)
            return

        running_thread = self._running_thread
        if running_thread is not None and running_thread.is_alive():
            self._emit_main_text_reply(
                "session busy — interrupt the current turn before switching models",
                turn_id,
            )
            return

        agent = self._agent
        switch_live_model = getattr(agent, "switch_model", None)
        if not callable(switch_live_model):
            self._emit_main_text_reply("Model switching is unavailable for this session.", turn_id)
            return

        current_provider = str(getattr(agent, "provider", "") or "")
        current_model = str(getattr(agent, "model", "") or "")
        current_base_url = str(getattr(agent, "base_url", "") or "")
        current_api_key = getattr(agent, "api_key", "")

        try:
            from hermes_cli.config import get_compatible_custom_providers, load_config
            from hermes_cli.model_switch import switch_model

            cfg = load_config()
            user_providers = cfg.get("providers")
            custom_providers = get_compatible_custom_providers(cfg)
            result = switch_model(
                raw_input=model_input,
                current_provider=current_provider,
                current_model=current_model,
                current_base_url=current_base_url,
                current_api_key=current_api_key,
                is_global=False,
                explicit_provider="",
                user_providers=user_providers,
                custom_providers=custom_providers,
            )
            if not result.success:
                raise ValueError(result.error_message or "model switch failed")

            target_provider = str(result.target_provider or "")
            target_api_key = result.api_key
            target_base_url = result.base_url
            target_api_mode = result.api_mode

            if target_provider == current_provider:
                if not target_api_key:
                    target_api_key = current_api_key
                if not target_base_url:
                    target_base_url = current_base_url
                if not target_api_mode:
                    target_api_mode = str(getattr(agent, "api_mode", "") or "")

            switch_live_model(
                result.new_model,
                target_provider,
                api_key=target_api_key,
                base_url=target_base_url,
                api_mode=target_api_mode,
            )
            _refresh_live_agent_runtime_after_model_switch(agent)
        except Exception as exc:
            self._emit_main_text_reply(f"Model switch failed: {exc}", turn_id)
            return

        response = f"model -> {result.new_model}"
        if result.warning_message:
            response = f"{response}\nwarning: {result.warning_message}"
        self._emit_main_text_reply(response, turn_id)

    def handle_message(
        self,
        text: str,
        *,
        client_msg_id: str | None = None,
        attachments: list[ChatAttachment] | None = None,
    ) -> None:
        stripped = str(text or "")
        turn_attachments = list(attachments or [])
        with self._lock:
            if self._pending_approval is not None:
                self._send_event(
                    "error",
                    {
                        "code": "waiting_for_approval",
                        "message": "Session is waiting for approval.",
                    },
                    source=self._foreground_source,
                )
                return
            if self._pending_clarify is not None:
                self._send_event(
                    "error",
                    {
                        "code": "waiting_for_clarify",
                        "message": "Session is waiting for clarify input.",
                    },
                    source=self._foreground_source,
                )
                return

            turn_id = f"turn_{uuid4().hex[:10]}"
            self._send_event(
                "message.accepted",
                {
                    "client_msg_id": client_msg_id,
                    "message_id": f"user_{uuid4().hex[:10]}",
                },
                source=self._foreground_source,
                turn_id=turn_id,
            )
            slash_parts = self._native_slash_parts(stripped)
            if slash_parts is not None:
                command, arg = slash_parts
                self._turn_id = turn_id
                if command == "/stop":
                    if self._handle_stop_slash(turn_id):
                        return
                if command == "/a2a":
                    self._handle_a2a_slash(turn_id)
                    return
                if command == "/help":
                    self._handle_help_slash(turn_id)
                    return
                if command == "/model":
                    self._handle_model_slash(arg, turn_id)
                    return
            if self._foreground_source == "delegate" and self._delegate_input is not None:
                self._turn_id = turn_id
                self._set_run_state("running", source="delegate")
                self._delegate_input.push_line(stripped)
                return

            if self._running_thread is not None and self._running_thread.is_alive():
                self._send_event(
                    "error",
                    {
                        "code": "session_busy",
                        "message": "Session is currently busy.",
                    },
                    source=self._foreground_source,
                    turn_id=turn_id,
                )
                return

            self._turn_id = turn_id
            self._main_message_id = f"assistant_{uuid4().hex[:10]}"
            self._foreground_source = "main"
            self._foreground_agent = ""
            self._set_run_state("running", source="main")
            worker = threading.Thread(
                target=self._run_turn,
                args=(stripped, turn_attachments, turn_id),
                name=f"aisoc-{self.session_id[:8]}",
                daemon=True,
            )
            self._running_thread = worker
            worker.start()

    def handle_approval_response(self, choice: str) -> None:
        normalized = str(choice or "").strip().lower()
        if normalized not in {"once", "session", "always", "deny"}:
            self._send_event(
                "error",
                {"code": "invalid_approval_choice", "message": "Invalid approval choice."},
                source=self._foreground_source,
                turn_id=self._turn_id,
            )
            return
        resolved = resolve_gateway_approval(self._runtime_session_id, normalized)
        if resolved <= 0:
            self._send_event(
                "error",
                {"code": "approval_not_pending", "message": "No pending approval for this session."},
                source=self._foreground_source,
                turn_id=self._turn_id,
            )
            return
        with self._lock:
            pending = self._pending_approval
            self._pending_approval = None
        self._send_event(
            "approval.resolved",
            {
                "approval_id": pending.approval_id if pending else None,
                "choice": normalized,
            },
            source=self._foreground_source,
            turn_id=self._turn_id,
        )
        self._set_run_state("running", source=self._foreground_source)

    def handle_clarify_response(self, answer: str) -> None:
        normalized = str(answer or "").strip()
        if not normalized:
            self._send_event(
                "error",
                {"code": "invalid_clarify_answer", "message": "Clarify answer must not be empty."},
                source=self._foreground_source,
                turn_id=self._turn_id,
            )
            return
        with self._lock:
            pending = self._pending_clarify
            if pending is None:
                pending = None
            else:
                self._pending_clarify = None
                pending.answer = normalized
                pending.event.set()
        if pending is None:
            self._send_event(
                "error",
                {"code": "clarify_not_pending", "message": "No pending clarify request for this session."},
                source=self._foreground_source,
                turn_id=self._turn_id,
            )
            return
        self._send_event(
            "clarify.resolved",
            {
                "clarify_id": pending.clarify_id,
                "answer": normalized,
            },
            source=self._foreground_source,
            turn_id=self._turn_id,
        )
        self._set_run_state("running", source=self._foreground_source)

    def interrupt(self) -> None:
        if self._cancel_active_remote_a2a_delegate(
            turn_id=self._turn_id or f"turn_{uuid4().hex[:10]}",
            source=self._foreground_source,
        ):
            return
        agent = self._agent
        interrupt = getattr(agent, "interrupt", None)
        if callable(interrupt):
            try:
                interrupt("Interrupted by websocket client.")
            except Exception:
                pass
        self._set_run_state("interrupted", source=self._foreground_source)

    def activate_delegate_input(self, input_adapter: ChatInputAdapter) -> bool:
        with self._lock:
            if self._delegate_input not in {None, input_adapter}:
                return False
            self._delegate_input = input_adapter
            self._foreground_source = "delegate"
            self._foreground_agent = self._pending_delegate_agent_name or self._foreground_agent or "delegate"
            self._foreground_state = DelegateForegroundState(
                child_session_id=None,
                srcagent=self._foreground_agent,
                reason=None,
            )
        self._set_run_state("waiting_for_delegate_input", source="delegate")
        self._send_event(
            "delegate.entered",
            {
                "child_session_id": self._foreground_state.child_session_id,
                "srcagent": self._foreground_agent,
            },
            source="delegate",
            turn_id=self._turn_id,
        )
        return True

    def release_delegate_input(self, input_adapter: ChatInputAdapter | None = None) -> None:
        with self._lock:
            if input_adapter is not None and self._delegate_input not in {None, input_adapter}:
                return
            srcagent = self._foreground_agent
            reason = self._foreground_state.reason or "completed"
            child_session_id = self._foreground_state.child_session_id
            self._delegate_input = None
            self._foreground_source = "main"
            self._foreground_agent = ""
            self._foreground_state = DelegateForegroundState()
        self._send_event(
            "delegate.exited",
            {
                "child_session_id": child_session_id,
                "srcagent": srcagent or None,
                "reason": reason,
            },
            source="delegate",
            turn_id=self._turn_id,
        )
        self._set_run_state("running", source="main")

    def handle_delegate_output(
        self,
        *,
        source: str,
        event_type: str,
        content: str,
        delegate_session_id: str | None = None,
    ) -> None:
        if source != "delegate":
            return

        if delegate_session_id:
            with self._lock:
                self._foreground_state.child_session_id = str(delegate_session_id)

        if event_type == "status":
            normalized = str(content or "").strip().lower()
            if "return to main" in normalized:
                with self._lock:
                    self._foreground_state.reason = "return_to_main"
            elif "interrupted" in normalized:
                with self._lock:
                    self._foreground_state.reason = "interrupted"
                self._set_run_state("interrupted", source="delegate")
            elif "entered foreground loop" in normalized:
                self._set_run_state("waiting_for_delegate_input", source="delegate")
            return

        if event_type == "tool_call":
            tool_name = str(content or "").strip().split(" ", 1)[0]
            self._send_event(
                "tool.started",
                {
                    "tool_name": tool_name,
                    "tool_call_id": f"delegate-tool-{uuid4().hex[:10]}",
                    "args_preview": content,
                    "srcagent": self._foreground_agent or None,
                },
                source="delegate",
                turn_id=self._turn_id,
            )
            return

        if event_type == "error":
            self._send_event(
                "error",
                {
                    "code": "delegate_error",
                    "message": content,
                    "srcagent": self._foreground_agent or None,
                },
                source="delegate",
                turn_id=self._turn_id,
            )
            self._set_run_state("error", source="delegate")
            return

        if event_type == "ai_delta":
            if not content or not _message_delta_enabled():
                return
            message_id = self._latest_delegate_message_id or f"delegate_msg_{uuid4().hex[:10]}"
            self._latest_delegate_message_id = message_id
            self._send_event(
                "message.delta",
                {
                    "message_id": message_id,
                    "delta": content,
                    "srcagent": self._foreground_agent or None,
                },
                source="delegate",
                turn_id=self._turn_id,
            )
            return

        if event_type == "ai":
            message_id = self._latest_delegate_message_id
            if message_id:
                self._send_event(
                    "message.stream.completed",
                    {
                        "message_id": message_id,
                        "completed": True,
                        "srcagent": self._foreground_agent or None,
                    },
                    source="delegate",
                    turn_id=self._turn_id,
                )
            else:
                self._send_event(
                    "message.completed",
                    {
                        "message_id": f"delegate_msg_{uuid4().hex[:10]}",
                        "content": content,
                        "completed": True,
                        "srcagent": self._foreground_agent or None,
                    },
                    source="delegate",
                    turn_id=self._turn_id,
                )
            self._latest_delegate_message_id = None

    def record_pending_delegate_name(self, function_args: dict[str, Any] | None) -> None:
        if not function_args:
            return
        delegate_name = (
            str(function_args.get("agent_name") or "").strip()
            or str(function_args.get("type") or "").strip()
            or "delegate"
        )
        with self._lock:
            self._pending_delegate_agent_name = delegate_name

    def _approval_notify_sync(self, approval_data: dict[str, Any]) -> None:
        approval_id = f"approval_{uuid4().hex[:10]}"
        with self._lock:
            self._pending_approval = ApprovalRequestState(
                approval_id=approval_id,
                command=str(approval_data.get("command") or ""),
                description=str(approval_data.get("description") or ""),
            )
        self._set_run_state("waiting_for_approval", source=self._foreground_source)
        self._send_event(
            "approval.request",
            {
                "approval_id": approval_id,
                "command": self._pending_approval.command,
                "description": self._pending_approval.description,
                "choices": list(self._pending_approval.choices),
            },
            source=self._foreground_source,
            turn_id=self._turn_id,
        )

    def _clarify_callback_sync(self, question: str, choices: list[str] | None) -> str:
        normalized_question = str(question or "").strip()
        normalized_choices = [str(choice).strip() for choice in (choices or []) if str(choice).strip()]
        pending = ClarifyRequestState(
            clarify_id=f"clarify_{uuid4().hex[:10]}",
            question=normalized_question,
            choices=normalized_choices or None,
            awaiting_text=not bool(normalized_choices),
        )
        with self._lock:
            self._pending_clarify = pending
        self._set_run_state("waiting_for_clarify", source=self._foreground_source)
        self._send_event(
            "clarify.request",
            {
                "clarify_id": pending.clarify_id,
                "question": pending.question,
                "choices": list(pending.choices or []) or None,
            },
            source=self._foreground_source,
            turn_id=self._turn_id,
        )
        if pending.event.wait(timeout=_clarify_timeout_seconds()):
            return str(pending.answer or "")
        with self._lock:
            if self._pending_clarify is pending:
                self._pending_clarify = None
        self._send_event(
            "clarify.resolved",
            {
                "clarify_id": pending.clarify_id,
            },
            source=self._foreground_source,
            turn_id=self._turn_id,
        )
        self._set_run_state("running", source=self._foreground_source)
        timeout_minutes = max(1, int(_clarify_timeout_seconds() / 60))
        return f"[user did not respond within {timeout_minutes}m]"

    def _run_turn(
        self,
        user_message: str,
        attachments: list[ChatAttachment],
        turn_id: str,
    ) -> None:
        agent = self._agent
        old_stream = getattr(agent, "stream_delta_callback", None)
        old_tool_start = getattr(agent, "tool_start_callback", None)
        old_tool_complete = getattr(agent, "tool_complete_callback", None)
        old_clarify_callback = getattr(agent, "clarify_callback", None)
        old_delegate_output = getattr(agent, "_delegate_ext_output_adapter", None)
        old_delegate_input_factory = getattr(agent, "_delegate_ext_input_factory", None)
        approval_token = None
        user_env_token = None
        session_tokens = []
        modified_files: list[str] = []
        modified_files_lock = threading.Lock()
        try:
            approval_token = set_current_session_key(self._runtime_session_id)
            if self._user_id:
                from tools.user_env_runtime import set_current_user_env_identity

                user_env_token = set_current_user_env_identity(
                    "aisoc", self._user_id, self._user_name
                )
            session_tokens = set_session_vars(
                platform=CHAT_PLATFORM,
                session_key=self._runtime_session_id,
                user_id=self._user_id,
                user_name=self._user_name,
            )
            register_gateway_notify(self._runtime_session_id, self._approval_notify_sync)

            def _on_delta(delta: str) -> None:
                if delta is None or not _message_delta_enabled():
                    return
                self._send_event(
                    "message.delta",
                    {
                        "message_id": self._main_message_id,
                        "delta": delta,
                    },
                    source="main",
                    turn_id=turn_id,
                )

            def _on_tool_start(tool_call_id: str, function_name: str, function_args: dict | None) -> None:
                if function_name == "a2a_delegate":
                    self.record_pending_delegate_name(function_args)
                self._send_event(
                    "tool.started",
                    {
                        "tool_name": function_name,
                        "tool_call_id": tool_call_id,
                        "args_preview": json.dumps(function_args or {}, ensure_ascii=False),
                    },
                    source=self._foreground_source,
                    turn_id=turn_id,
                )

            def _on_tool_complete(
                tool_call_id: str,
                function_name: str,
                function_args: dict | None,
                function_result: object,
            ) -> None:
                if self._foreground_source == "main":
                    landed_files = _successful_mutation_paths(
                        function_name,
                        function_args,
                        function_result,
                    )
                    if landed_files:
                        with modified_files_lock:
                            known_files = set(modified_files)
                            modified_files.extend(
                                path for path in landed_files if path not in known_files
                            )
                preview = str(function_result)
                if len(preview) > 200:
                    preview = preview[:200] + "..."
                event_source = self._foreground_source
                self._send_event(
                    "tool.completed",
                    {
                        "tool_name": function_name,
                        "tool_call_id": tool_call_id,
                        "result_preview": preview,
                    },
                    source=event_source,
                    turn_id=turn_id,
                )
                if event_source == "main" and function_name == "a2a_delegate":
                    self._send_event(
                        "message.stream.completed",
                        {
                            "message_id": self._main_message_id,
                            "completed": True,
                        },
                        source="main",
                        turn_id=turn_id,
                    )
                    self._main_message_id = f"assistant_{uuid4().hex[:10]}"

            setattr(agent, "stream_delta_callback", _on_delta)
            setattr(agent, "tool_start_callback", _on_tool_start)
            setattr(agent, "tool_complete_callback", _on_tool_complete)
            setattr(agent, "clarify_callback", self._clarify_callback_sync)
            setattr(agent, "_delegate_ext_output_adapter", self._output_adapter)
            setattr(
                agent,
                "_delegate_ext_input_factory",
                lambda: ChatInputAdapter(
                    on_enter_foreground=self.activate_delegate_input,
                    on_exit_foreground=self.release_delegate_input,
                ),
            )

            history = load_conversation_history(agent, self._runtime_session_id)
            prepared_message = prepare_turn_message(user_message, attachments, agent=agent)
            result = agent.run_conversation(
                user_message=prepared_message,
                conversation_history=history,
                task_id=turn_id,
            )
            self._heal_session_owner()
            final_response = str(result.get("final_response") or "")
            if final_response:
                with modified_files_lock:
                    completed_files = list(modified_files)
                self._send_event(
                    "message.completed",
                    {
                        "message_id": self._main_message_id,
                        "content": final_response,
                        "completed": bool(result.get("completed", True)),
                        "modified_files": completed_files or None,
                    },
                    source="main",
                    turn_id=turn_id,
                )
            if result.get("failed"):
                self._set_run_state("error", source=self._foreground_source)
                if result.get("error"):
                    self._send_event(
                        "error",
                        {
                            "code": "run_failed",
                            "message": str(result.get("error")),
                        },
                        source=self._foreground_source,
                        turn_id=turn_id,
                    )
            else:
                self._set_run_state("idle", source="main")
        except Exception as exc:
            self._set_run_state("error", source=self._foreground_source)
            self._send_event(
                "error",
                {
                    "code": "run_exception",
                    "message": str(exc),
                },
                source=self._foreground_source,
                turn_id=turn_id,
            )
        finally:
            unregister_gateway_notify(self._runtime_session_id)
            if approval_token is not None:
                try:
                    reset_current_session_key(approval_token)
                except Exception:
                    pass
            if session_tokens:
                try:
                    clear_session_vars(session_tokens)
                except Exception:
                    pass
            if user_env_token is not None:
                try:
                    from tools.user_env_runtime import reset_current_user_env_identity

                    reset_current_user_env_identity(user_env_token)
                except Exception:
                    pass
            if old_stream is not None:
                setattr(agent, "stream_delta_callback", old_stream)
            if old_tool_start is not None:
                setattr(agent, "tool_start_callback", old_tool_start)
            if old_tool_complete is not None:
                setattr(agent, "tool_complete_callback", old_tool_complete)
            if old_clarify_callback is not None:
                setattr(agent, "clarify_callback", old_clarify_callback)
            else:
                try:
                    delattr(agent, "clarify_callback")
                except Exception:
                    pass
            if old_delegate_output is not None:
                setattr(agent, "_delegate_ext_output_adapter", old_delegate_output)
            else:
                try:
                    delattr(agent, "_delegate_ext_output_adapter")
                except Exception:
                    pass
            if old_delegate_input_factory is not None:
                setattr(agent, "_delegate_ext_input_factory", old_delegate_input_factory)
            else:
                try:
                    delattr(agent, "_delegate_ext_input_factory")
                except Exception:
                    pass
            with self._lock:
                self._running_thread = None

    def _set_run_state(self, state: str, *, source: str) -> None:
        with self._lock:
            self._last_run_state = state
            self._last_state_source = source
        self._send_event(
            "run.state",
            {
                "state": state,
                "srcagent": self._foreground_agent or None,
            },
            source=source,
            turn_id=self._turn_id,
        )

    def _make_event(
        self,
        event_type: str,
        *,
        payload: dict[str, Any],
        source: str,
        turn_id: str | None,
    ) -> dict[str, Any]:
        with self._lock:
            self._event_counter += 1
            envelope = ChatEventEnvelope(
                type=event_type,
                session_id=self.session_id,
                server_event_id=(
                    f"{self.session_id}:{self._event_epoch}:{self._event_counter}"
                ),
                ts=_now_timestamp(),
                turn_id=turn_id,
                source=source,
                payload={key: value for key, value in payload.items() if value is not None},
            )
        return envelope.to_dict()

    def _send_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        source: str,
        turn_id: str | None = None,
    ) -> None:
        event = self._make_event(
            event_type,
            payload=payload,
            source=source,
            turn_id=turn_id,
        )
        loop = None
        websocket = None
        with self._lock:
            loop = self._loop
            websocket = self._websocket
        if loop is None or websocket is None:
            return

        async def _do_send() -> None:
            try:
                await websocket.send_json(event)
            except Exception:
                return

        try:
            asyncio.run_coroutine_threadsafe(_do_send(), loop)
        except Exception:
            return


class ChatSessionManager:
    def __init__(self, agent_factory: Callable[..., object] | None = None) -> None:
        self._agent_factory = agent_factory or _build_default_aisoc_agent
        self._lock = threading.Lock()
        self._sessions: dict[tuple[str, str], ChatSessionActor] = {}
        self.attachments = ChatAttachmentStore()

    def set_agent_factory(self, agent_factory: Callable[..., object]) -> None:
        with self._lock:
            self._agent_factory = agent_factory
            self._sessions.clear()

    def bind(
        self,
        websocket: WebSocket,
        loop: asyncio.AbstractEventLoop,
        *,
        session_id: str | None,
        title: str | None,
        user_id: str | None = None,
        user_name: str | None = None,
    ) -> ChatSessionActor:
        resolved_session_id = str(session_id or "").strip() or f"aisoc-{uuid4().hex}"
        owner_key = str(user_id or "").strip()
        session_key = (owner_key, resolved_session_id)
        with self._lock:
            actor = self._sessions.get(session_key)
            if actor is None:
                actor = ChatSessionActor(
                    session_id=resolved_session_id,
                    title=_conversation_title(title),
                    agent_factory=self._agent_factory,
                    user_id=user_id,
                    user_name=user_name,
                )
                self._sessions[session_key] = actor
            else:
                actor.set_title(title)
                actor.update_identity(user_id=user_id, user_name=user_name)
        actor.replace_connection(websocket, loop)
        return actor

    def get(self, session_id: str, *, user_id: str | None = None) -> ChatSessionActor | None:
        with self._lock:
            if user_id is not None:
                return self._sessions.get((str(user_id).strip(), session_id))
            matches = [
                actor
                for (_owner_key, public_session_id), actor in self._sessions.items()
                if public_session_id == session_id
            ]
            return matches[0] if len(matches) == 1 else None
