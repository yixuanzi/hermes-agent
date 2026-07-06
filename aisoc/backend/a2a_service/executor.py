"""Official A2A SDK executor implementation for AISOC."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import inspect
import json
import logging
import re as _re

from a2a.helpers.proto_helpers import new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState

from hermes_state import SessionDB
from hermes_cli import config as hermes_config
from hermes_cli import runtime_provider

from aisoc.backend.agent_runtime import (
    build_profile_agent_kwargs,
    default_agent_factory,
    load_conversation_history,
)

from tools.user_env_runtime import (
    set_current_user_env_identity,
    reset_current_user_env_identity,
)
from gateway.session_context import set_session_vars, clear_session_vars

from .converter import a2a_to_text, history_to_a2a, text_to_message


AgentFactory = Callable[[str], object]


logger = logging.getLogger(__name__)


def _default_agent_factory(session_id: str):
    return default_agent_factory(
        session_id,
        platform="aisoc-a2a",
        config_module=hermes_config,
        runtime_provider_module=runtime_provider,
        session_db_cls=SessionDB,
        log=logger,
    )


def _profile_agent_kwargs(session_id: str) -> dict[str, object]:
    """Resolve the current profile into explicit AIAgent kwargs."""
    return build_profile_agent_kwargs(
        session_id,
        platform="aisoc-a2a",
        config_module=hermes_config,
        runtime_provider_module=runtime_provider,
    )


class HermesA2AExecutor(AgentExecutor):
    """Bridge the official A2A SDK to Hermes conversations."""

    _STREAM_DONE = object()
    _CALLBACK_MISSING = object()

    def __init__(
        self,
        agent_factory: AgentFactory | None = None,
        *,
        enable_streaming: bool = False,
    ):
        self._agent_factory = agent_factory or _default_agent_factory
        self._enable_streaming = enable_streaming
        self._agents: dict[str, object] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.message is None:
            raise ValueError("A2A request missing message payload.")

        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            context.current_task = task
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        cancel_event = asyncio.Event()
        async with self._lock:
            self._cancel_events[task.id] = cancel_event

        user_input = a2a_to_text(context.message)

        # ── 解析 <source> 前缀（字段灵活，不假设字段数量）─────────────────────
        _source_meta: dict = {}
        _src_m = _re.match(r"^<source>(\{.*?\})</source>\s*\n*", user_input, _re.DOTALL)
        if _src_m:
            try:
                _source_meta = json.loads(_src_m.group(1))
            except (json.JSONDecodeError, ValueError):
                pass
            # 保留前缀，LLM 可见完整原文

        _src_platform = _source_meta.get("platform", "")
        _src_uid      = _source_meta.get("uid", "")
        _src_uname    = _source_meta.get("uname", "")

        # ── 路径 A：直接绑定 userenv ContextVar（userenv tool 的优先读取路径）──
        _identity_token = None
        if _src_uid:
            _identity_token = set_current_user_env_identity(_src_platform, _src_uid, _src_uname)

        # ── 路径 B：绑定 HERMES_SESSION_* ContextVar（SOUL.md / session context）
        _session_tokens = set_session_vars(
            platform=_src_platform,
            chat_id=_source_meta.get("channel", ""),
            user_id=_src_uid,
            user_name=_src_uname,
        )

        await updater.start_work()

        if user_input == "__input_required__":
            await updater.requires_input(
                updater.new_agent_message([text_to_message("Need more information.").parts[0]])
            )
            return

        if user_input == "__fail__":
            await updater.failed(
                updater.new_agent_message([text_to_message("boom").parts[0]])
            )
            return

        if user_input == "__cancel__":
            try:
                await asyncio.wait_for(cancel_event.wait(), timeout=10.0)
            except TimeoutError:
                await updater.failed(
                    updater.new_agent_message(
                        [text_to_message("Cancellation timeout.").parts[0]]
                    )
                )
            return

        agent = await self._get_agent(task.context_id)
        agent._pending_source_meta = _source_meta  # per-request 注入，供 _run_agent_conversation 使用

        # ── 路径 C：构建 Session Context Prompt，动态注入 agent.ephemeral_system_prompt ──
        # build_session_context_prompt() 的输出会在每次 API call 时拼入 effective_system，
        # 让 LLM 能实时感知来源平台、用户身份等上下文（而不只是工具层的 ContextVar）。
        _context_prompt: str | None = None
        if _src_platform:
            try:
                from gateway.session import (
                    SessionSource,
                    SessionContext,
                    build_session_context_prompt,
                )
                from gateway.config import Platform

                # ── Platform 容错：aegis / aisoc-a2a 等 A2A 来源不在枚举中，
                #    用 try/except 降级到 API_SERVER（保证类型合法），
                #    同时保留原始字符串供后续替换真实来源名。
                try:
                    _plat_enum = Platform(_src_platform)
                    _plat_is_fallback = False
                except (ValueError, KeyError):
                    logger.warning(
                        "executor path-C: unknown platform %r, "
                        "falling back to API_SERVER for type safety. "
                        "Session context will reflect original platform name.",
                        _src_platform,
                    )
                    _plat_enum = Platform.API_SERVER
                    _plat_is_fallback = True

                _source_obj = SessionSource(
                    platform=_plat_enum,
                    chat_id=_source_meta.get("channel", ""),
                    user_id=_src_uid,
                    user_name=_src_uname,
                )
                _session_ctx_obj = SessionContext(
                    source=_source_obj,
                    connected_platforms=[_plat_enum],
                    home_channels={},
                )
                _context_prompt = build_session_context_prompt(_session_ctx_obj)

                # fallback 场景：把 prompt 里 API_SERVER 生成的"Source: API"
                # 替换成真实来源名，避免语义丢失。
                if _plat_is_fallback and _context_prompt:
                    _context_prompt = _context_prompt.replace(
                        "**Source:** API",
                        f"**Source:** {_src_platform} (A2A)",
                    )

            except Exception as e:
                logger.warning(
                    "executor path-C: failed to build session context prompt "
                    "for platform=%r uid=%r, falling back to minimal context. error=%s",
                    _src_platform,
                    _src_uid,
                    e,
                )
                # 兜底：手动构造最小 context，确保 _context_prompt 不为 None，
                # LLM 始终能感知来源平台和用户身份。
                _context_prompt = (
                    f"**Source:** {_src_platform} (A2A)\n"
                    f"**User:** {_src_uname}\n"
                    f"**User ID:** {_src_uid}\n"
                )
        agent._pending_context_prompt = _context_prompt  # 传给 _run_agent_conversation

        history = load_conversation_history(
            agent,
            getattr(agent, "session_id", None) or task.context_id,
        )
        delta_queue: asyncio.Queue[object] | None = None
        stream_task: asyncio.Task[str] | None = None
        stream_callback = None
        tool_start_callback = None
        tool_complete_callback = None
        loop = asyncio.get_running_loop()
        if self._enable_streaming:
            delta_queue = asyncio.Queue()
            stream_task = asyncio.create_task(
                self._drain_stream_deltas(delta_queue, updater)
            )
            tool_start_callback = self._make_tool_start_callback(loop, delta_queue)
            tool_complete_callback = self._make_tool_complete_callback(loop, delta_queue)
            if self._agent_accepts_stream_callback(agent):
                stream_callback = self._make_stream_callback(loop, delta_queue)
        try:
            result = await asyncio.to_thread(
                self._run_agent_conversation,
                agent,
                user_input,
                history,
                task.id,
                stream_callback,
                tool_start_callback,
                tool_complete_callback,
            )
        except Exception as exc:
            await self._finish_stream_consumer(loop, delta_queue, stream_task)
            await updater.failed(
                updater.new_agent_message([text_to_message(str(exc)).parts[0]])
            )
            return
        finally:
            if _identity_token is not None:
                reset_current_user_env_identity(_identity_token)
            clear_session_vars(_session_tokens)
            async with self._lock:
                self._cancel_events.pop(task.id, None)

        streamed_text = await self._finish_stream_consumer(loop, delta_queue, stream_task)
        response_text = str(result.get("final_response") or "")
        if not response_text and streamed_text:
            response_text = streamed_text
        response_message = updater.new_agent_message(
            [text_to_message(response_text, context_id=task.context_id, task_id=task.id).parts[0]]
        )
        if bool(result.get("input_required")):
            await updater.requires_input(response_message)
        else:
            await updater.complete(response_message)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            return
        async with self._lock:
            event = self._cancel_events.setdefault(task.id, asyncio.Event())
            event.set()
        agent = self._agents.get(task.context_id)
        if agent is not None:
            setattr(agent, "_interrupt_requested", True)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.cancel(
            updater.new_agent_message([text_to_message("Canceled by user.").parts[0]])
        )

    async def export_history(
        self, context_id: str, task_id: str
    ) -> list:
        """Expose stored history in A2A message format for tests and diagnostics."""
        agent = self._agents.get(context_id)
        session_id = getattr(agent, "session_id", None) if agent is not None else context_id
        history = load_conversation_history(agent, session_id) if agent is not None else []
        return history_to_a2a(history, context_id=context_id, task_id=task_id)

    async def _get_agent(self, context_id: str):
        async with self._lock:
            agent = self._agents.get(context_id)
            if agent is None:
                agent = self._agent_factory(context_id)
                self._agents[context_id] = agent
            return agent

    def _agent_accepts_stream_callback(self, agent: object) -> bool:
        try:
            signature = inspect.signature(agent.run_conversation)
        except (TypeError, ValueError, AttributeError):
            return False

        if "stream_callback" in signature.parameters:
            return True
        return any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )

    def _make_stream_callback(
        self,
        loop: asyncio.AbstractEventLoop,
        delta_queue: asyncio.Queue[object],
    ) -> Callable[[str | None], None]:
        def _callback(delta: str | None) -> None:
            loop.call_soon_threadsafe(delta_queue.put_nowait, delta)

        return _callback

    def _make_tool_start_callback(
        self,
        loop: asyncio.AbstractEventLoop,
        delta_queue: asyncio.Queue[object],
    ) -> Callable[[str, str, dict | None], None]:
        def _callback(tool_call_id: str, function_name: str, function_args: dict | None) -> None:
            loop.call_soon_threadsafe(
                delta_queue.put_nowait,
                {
                    "kind": "tool_call",
                    "tool_call_id": tool_call_id,
                    "name": function_name,
                    "arguments": function_args or {},
                },
            )

        return _callback

    def _make_tool_complete_callback(
        self,
        loop: asyncio.AbstractEventLoop,
        delta_queue: asyncio.Queue[object],
    ) -> Callable[[str, str, dict | None, object], None]:
        def _callback(
            tool_call_id: str,
            function_name: str,
            function_args: dict | None,
            function_result: object,
        ) -> None:
            loop.call_soon_threadsafe(
                delta_queue.put_nowait,
                {
                    "kind": "tool_result",
                    "tool_call_id": tool_call_id,
                    "name": function_name,
                    "arguments": function_args or {},
                    "result": str(function_result),
                },
            )

        return _callback

    def _new_tool_event_message(self, updater: TaskUpdater, payload: dict[str, object]):
        hermes_metadata = {
            "kind": str(payload.get("kind") or ""),
            "tool_call_id": str(payload.get("tool_call_id") or ""),
            "name": str(payload.get("name") or ""),
        }
        arguments = payload.get("arguments")
        if arguments not in (None, {}):
            hermes_metadata["arguments"] = arguments
        result = payload.get("result")
        parts = []
        if result not in (None, ""):
            hermes_metadata["result"] = str(result)
            parts = [text_to_message(str(result)).parts[0]]
        elif arguments not in (None, {}):
            parts = [text_to_message(json.dumps(arguments, ensure_ascii=True, separators=(",", ":"))).parts[0]]
        return updater.new_agent_message(parts, metadata={"hermes": hermes_metadata})

    async def _drain_stream_deltas(
        self,
        delta_queue: asyncio.Queue[object],
        updater: TaskUpdater,
    ) -> str:
        accumulated = ""
        while True:
            delta = await delta_queue.get()
            if delta is self._STREAM_DONE:
                return accumulated
            if delta is None:
                continue
            if isinstance(delta, dict):
                await updater.update_status(
                    TaskState.TASK_STATE_WORKING,
                    self._new_tool_event_message(updater, delta),
                )
                continue

            text = str(delta)
            if not text:
                continue
            accumulated += text
            await updater.update_status(
                TaskState.TASK_STATE_WORKING,
                updater.new_agent_message([text_to_message(accumulated).parts[0]]),
            )

    async def _finish_stream_consumer(
        self,
        loop: asyncio.AbstractEventLoop,
        delta_queue: asyncio.Queue[object] | None,
        stream_task: asyncio.Task[str] | None,
    ) -> str:
        if delta_queue is None or stream_task is None:
            return ""
        loop.call_soon_threadsafe(delta_queue.put_nowait, self._STREAM_DONE)
        return await stream_task

    def _run_agent_conversation(
        self,
        agent: object,
        user_input: str,
        history: list[dict[str, str]],
        task_id: str,
        stream_callback,
        tool_start_callback=None,
        tool_complete_callback=None,
    ) -> dict[str, object]:
        # ── 临时注入 agent 身份属性（tool_executor.py L876/L906 从 agent 属性读 userenv 分区键）
        src = getattr(agent, "_pending_source_meta", {})
        _uid  = src.get("uid", "")
        _uname = src.get("uname", "")
        _plat  = src.get("platform", "")
        _old_uid      = getattr(agent, "_user_id", "")
        _old_uname    = getattr(agent, "_user_name", "")
        _old_plat     = getattr(agent, "_user_env_platform", self._CALLBACK_MISSING)
        _old_platform = getattr(agent, "platform", self._CALLBACK_MISSING)
        if _uid:
            agent._user_id           = _uid
            agent._user_name         = _uname
            agent._user_env_platform = _plat  # tool_executor L877 优先读此属性，fallback agent.platform
        if _plat:
            agent.platform           = _plat  # 同步覆盖 agent.platform，确保 _format_aegis_source_header 读到真实来源平台

        # ── 路径 C：临时覆写 ephemeral_system_prompt，让 LLM 每次都能看到实时 Session Context ──
        # agent.ephemeral_system_prompt 在每次 API call 前实时拼入 effective_system（不走
        # _cached_system_prompt 缓存），因此每次请求覆写都会立即生效，结束后还原，对下一次请求无污染。
        _context_prompt = getattr(agent, "_pending_context_prompt", None)
        _old_ephemeral = getattr(agent, "ephemeral_system_prompt", None)
        if _context_prompt:
            agent.ephemeral_system_prompt = _context_prompt

        kwargs: dict[str, object] = {}
        if stream_callback is not None:
            kwargs["stream_callback"] = stream_callback
        old_tool_start = getattr(agent, "tool_start_callback", self._CALLBACK_MISSING)
        old_tool_complete = getattr(agent, "tool_complete_callback", self._CALLBACK_MISSING)
        try:
            if tool_start_callback is not None:
                setattr(agent, "tool_start_callback", tool_start_callback)
            if tool_complete_callback is not None:
                setattr(agent, "tool_complete_callback", tool_complete_callback)
            return agent.run_conversation(
                user_input,
                None,
                history,
                task_id,
                **kwargs,
            )
        finally:
            # 恢复 agent 身份属性
            if _uid:
                agent._user_id   = _old_uid
                agent._user_name = _old_uname
                if _old_plat is self._CALLBACK_MISSING:
                    try:
                        delattr(agent, "_user_env_platform")
                    except Exception:
                        pass
                else:
                    agent._user_env_platform = _old_plat
            # 恢复 agent.platform
            if _plat:
                if _old_platform is self._CALLBACK_MISSING:
                    try:
                        delattr(agent, "platform")
                    except Exception:
                        pass
                else:
                    agent.platform = _old_platform
            # 恢复 ephemeral_system_prompt
            if _context_prompt:
                agent.ephemeral_system_prompt = _old_ephemeral
            # 恢复原有回调属性
            if old_tool_start is self._CALLBACK_MISSING:
                try:
                    delattr(agent, "tool_start_callback")
                except Exception:
                    pass
            else:
                setattr(agent, "tool_start_callback", old_tool_start)
            if old_tool_complete is self._CALLBACK_MISSING:
                try:
                    delattr(agent, "tool_complete_callback")
                except Exception:
                    pass
            else:
                setattr(agent, "tool_complete_callback", old_tool_complete)
