"""Streamable-HTTP MCP surface for the WORKAGENT A2A module.

Served at ``/mcp`` beside the A2A JSON-RPC route, it exposes the same task
lifecycle as MCP tools.  Task tools drive the A2A request handler rather than
the executor directly, so an MCP task is an ordinary A2A task: it lives in the
same bounded task store, runs through the same executor, is cancellable from
either surface, and its ``session_id`` is the A2A ``context_id`` — the Hermes
session the executor keys its agent and conversation history on.  The
approval / clarify tools answer through the executor's interaction registry,
exactly as the A2A interaction-response HTTP route does.

Auth is not handled here: ``/mcp`` is not a public path, so the A2A bearer
middleware already guards it with the A2A session token.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Annotated, Any, Literal
import uuid

from a2a.server.context import ServerCallContext
from a2a.server.request_handlers import RequestHandler
from a2a.types import (
    CancelTaskRequest,
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    Task,
    TaskState,
)
from a2a.utils.errors import A2AError, TaskNotCancelableError, TaskNotFoundError
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.routing import BaseRoute

from .interactions import InteractionError

if TYPE_CHECKING:
    from .executor import HermesA2AExecutor


logger = logging.getLogger(__name__)

MCP_PATH = "/mcp"

_TERMINAL_STATES = frozenset(
    {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_REJECTED,
    }
)
# The turn has stopped and is waiting on the caller; a get_task wait ends here too.
_INTERRUPTED_STATES = frozenset(
    {TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED}
)

_POLL_SECONDS = 0.5
_WAIT_FOREVER = -1

_INSTRUCTIONS = (
    "Run Hermes agent tasks. start_task sends a message to the agent and returns "
    "task_id and session_id (mode='async', default) or waits until the task "
    "finishes or needs an interaction (mode='sync'); pass session_id back to "
    "continue the same conversation. After an interaction is answered, the "
    "result is only available through get_task. Poll get_task (timeout_seconds=-1 blocks until the task needs "
    "you) until done is true. While needs_interaction is true the task is paused "
    "on the items in pending_interactions: answer each approval_request with "
    "respond_approval and each clarify_request with respond_clarify, then keep "
    "polling. stop_task cancels a running task."
)


def _state_name(state: int) -> str:
    return TaskState.Name(state).removeprefix("TASK_STATE_").lower()


def _message_text(message: Message) -> str:
    return "\n".join(part.text for part in message.parts if part.HasField("text")).strip()


def create_task_mcp_server(
    request_handler: RequestHandler,
    executor: HermesA2AExecutor,
    *,
    name: str,
) -> MCPServer:
    """Build the MCP server whose tools run tasks through ``request_handler``."""
    mcp = MCPServer(name=name, instructions=_INSTRUCTIONS)
    interactions = executor.interactions

    def _call_context() -> ServerCallContext:
        # Same owner scope as an A2A JSON-RPC call (no per-user A2A identity),
        # so tasks are visible to both surfaces.
        return ServerCallContext()

    def _pending(task: Task) -> list[dict[str, Any]]:
        # An interaction only blocks a turn that is still running; the registry
        # cancels a task's records when its turn ends.
        if task.status.state != TaskState.TASK_STATE_WORKING:
            return []
        return interactions.pending_for_task(task.id)

    def _settled(task: Task) -> bool:
        state = task.status.state
        return state in _TERMINAL_STATES or state in _INTERRUPTED_STATES or bool(_pending(task))

    def _view(task: Task) -> dict[str, Any]:
        state = task.status.state
        status_text = (
            _message_text(task.status.message) if task.status.HasField("message") else ""
        )
        pending = _pending(task)
        return {
            "task_id": task.id,
            "session_id": task.context_id,
            "state": _state_name(state),
            "done": state in _TERMINAL_STATES,
            "result": status_text if state == TaskState.TASK_STATE_COMPLETED else None,
            # Failure reason, the question behind input_required, or the
            # partial streamed answer while the task is still working.
            "message": (
                status_text if state != TaskState.TASK_STATE_COMPLETED and status_text else None
            ),
            "needs_interaction": bool(pending),
            "pending_interactions": pending,
        }

    async def _get(task_id: str) -> Task:
        try:
            task = await request_handler.on_get_task(GetTaskRequest(id=task_id), _call_context())
        except TaskNotFoundError as exc:
            raise ToolError(f"task {task_id} not found") from exc
        if task is None:
            raise ToolError(f"task {task_id} not found")
        return task

    async def _wait(task: Task, timeout_seconds: float) -> Task:
        """Poll until ``task`` settles or ``timeout_seconds`` passes (-1: no limit)."""
        deadline = None if timeout_seconds == _WAIT_FOREVER else time.monotonic() + timeout_seconds
        while not _settled(task) and (deadline is None or time.monotonic() < deadline):
            await asyncio.sleep(_POLL_SECONDS)
            task = await _get(task.id)
        return task

    async def _resolve(task_id: str, interaction_id: str, kind: str, **answer: Any) -> dict[str, Any]:
        task_id, interaction_id = task_id.strip(), interaction_id.strip()
        task = await _get(task_id)
        try:
            record = interactions.resolve(
                {
                    "task_id": task_id,
                    "context_id": task.context_id,
                    "interaction_id": interaction_id,
                    "kind": kind,
                    **answer,
                }
            )
        except InteractionError as exc:
            raise ToolError(f"{kind} {interaction_id} not accepted: {exc.detail}") from exc
        try:
            await executor.publish_interaction_resolved(record)
        except Exception:
            # The agent's wait is already released; reporting failure here
            # would invite a retry that the registry then refuses as a duplicate.
            logger.warning(
                "Failed to publish resolved MCP interaction %s", interaction_id, exc_info=True
            )
        return {
            "interaction_id": record.interaction_id,
            "kind": record.kind,
            "state": record.state,
            "task": _view(await _get(task_id)),
        }

    @mcp.tool(
        name="start_task",
        description=(
            "Start an agent task. mode='async' (default) returns at once with "
            "task_id and session_id; follow it with get_task. mode='sync' waits "
            "until the task finishes or needs an interaction. If it returns with "
            "needs_interaction true, answer each item in pending_interactions with "
            "respond_approval / respond_clarify, then fetch the result with "
            "get_task. Pass session_id to continue an existing conversation; omit "
            "it to start a new one."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True),
    )
    async def start_task(
        message: Annotated[str, Field(description="The instruction or question for the agent.")],
        session_id: Annotated[
            str | None,
            Field(description="Existing session to continue; omit to start a new session."),
        ] = None,
        mode: Annotated[
            Literal["sync", "async"],
            Field(
                description=(
                    "async: return immediately. sync: wait until the task finishes "
                    "or needs an approval / clarify / input."
                )
            ),
        ] = "async",
    ) -> dict[str, Any]:
        text = message.strip()
        if not text:
            raise ToolError("message must not be empty")
        context_id = (session_id or "").strip() or uuid.uuid4().hex
        request = SendMessageRequest(
            message=Message(
                role=Role.ROLE_USER,
                message_id=uuid.uuid4().hex,
                context_id=context_id,
                parts=[Part(text=text)],
            ),
            configuration=SendMessageConfiguration(return_immediately=True),
        )
        try:
            started = await request_handler.on_message_send(request, _call_context())
        except A2AError as exc:
            raise ToolError(f"could not start task: {exc}") from exc
        if not isinstance(started, Task):
            raise ToolError("agent answered without creating a task")
        if mode == "async":
            return _view(started)
        # Started with return_immediately and then waited on the task store, so
        # a client that disconnects mid-wait leaves the task running for get_task.
        return _view(await _wait(started, _WAIT_FOREVER))

    @mcp.tool(
        name="get_task",
        description=(
            "Get a task's state and, once completed, its result. done is true when "
            "the task reached a final state (completed, failed, canceled, rejected). "
            "needs_interaction is true while the task waits on an approval or "
            "clarify request listed in pending_interactions; answer it with "
            "respond_approval / respond_clarify."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    )
    async def get_task(
        task_id: Annotated[str, Field(description="task_id returned by start_task.")],
        timeout_seconds: Annotated[
            float,
            Field(
                ge=_WAIT_FOREVER,
                description=(
                    "How long to wait for the task to finish or to need an "
                    "interaction (approval, clarify, input). 0 returns the current "
                    "status at once; -1 waits with no time limit; a positive value "
                    "waits at most that many seconds."
                ),
            ),
        ] = 0,
    ) -> dict[str, Any]:
        if timeout_seconds < 0 and timeout_seconds != _WAIT_FOREVER:
            raise ToolError("timeout_seconds must be -1, 0 or a positive number")
        task = await _wait(await _get(task_id.strip()), timeout_seconds)
        view = _view(task)
        view["timed_out"] = timeout_seconds != 0 and not _settled(task)
        return view

    @mcp.tool(
        name="respond_approval",
        description=(
            "Answer an approval_request from get_task's pending_interactions. "
            "choice: once (allow this time), session (allow for this session), "
            "always (allow permanently), deny."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False),
    )
    async def respond_approval(
        task_id: Annotated[str, Field(description="task_id of the waiting task.")],
        interaction_id: Annotated[
            str, Field(description="interaction_id of the approval_request.")
        ],
        choice: Annotated[
            Literal["once", "session", "always", "deny"],
            Field(description="The approval decision."),
        ],
    ) -> dict[str, Any]:
        return await _resolve(task_id, interaction_id, "approval", choice=choice)

    @mcp.tool(
        name="respond_clarify",
        description=(
            "Answer a clarify_request from get_task's pending_interactions. Give "
            "one string, or a list of strings when the request has multi_select."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False),
    )
    async def respond_clarify(
        task_id: Annotated[str, Field(description="task_id of the waiting task.")],
        interaction_id: Annotated[
            str, Field(description="interaction_id of the clarify_request.")
        ],
        answer: Annotated[
            str | list[str],
            Field(description="The answer: one of the offered choices or free text."),
        ],
    ) -> dict[str, Any]:
        return await _resolve(task_id, interaction_id, "clarify", answer=answer)

    @mcp.tool(
        name="stop_task",
        description="Stop (cancel) a running task. Fails if the task already finished.",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True),
    )
    async def stop_task(
        task_id: Annotated[str, Field(description="task_id returned by start_task.")],
    ) -> dict[str, Any]:
        task_id = task_id.strip()
        try:
            task = await request_handler.on_cancel_task(CancelTaskRequest(id=task_id), _call_context())
        except TaskNotFoundError as exc:
            raise ToolError(f"task {task_id} not found") from exc
        except TaskNotCancelableError as exc:
            current = await _get(task_id)
            raise ToolError(
                f"task {task_id} cannot be stopped: it is {_state_name(current.status.state)}"
            ) from exc
        except A2AError as exc:
            raise ToolError(f"could not stop task {task_id}: {exc}") from exc
        if task is None:
            raise ToolError(f"task {task_id} not found")
        return _view(task)

    return mcp


def mount_task_mcp(mcp: MCPServer, *, host: str) -> list[BaseRoute]:
    """Return the ``/mcp`` route; the caller must run ``mcp.session_manager.run()``.

    ``host`` keeps the SDK's DNS-rebinding guard on for loopback binds; a
    public bind (``--insecure``) relies on the bearer token alone, as the A2A
    route does.
    """
    starlette_app = mcp.streamable_http_app(streamable_http_path=MCP_PATH, host=host)
    return list(starlette_app.routes)
