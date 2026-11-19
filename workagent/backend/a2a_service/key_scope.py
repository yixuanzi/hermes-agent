"""Where a personal key's identity and isolation are applied, for every surface.

A personal key (``mcp_keys``) may call ``/a2a`` (JSON-RPC, its interaction and
steer routes) and ``/mcp``.  Both surfaces drive the same A2A request handler
and executor, so the key is enforced at the three seams they share rather
than per surface:

* ``PersonalKeyCallContextBuilder`` turns the key the auth middleware put on
  ``request.state`` into the A2A call context: the key is its own task-store
  owner, so ``tasks/get``, ``tasks/cancel``, ``tasks/list`` and subscriptions
  only ever see that key's tasks;
* ``KeyScopedRequestContextBuilder`` keeps a key inside its own session
  namespace: a new conversation gets a ``mk-<id>-`` context id and any other
  context id is refused, so a key cannot continue someone else's session;
* ``OwnerScopedRequestHandler`` closes the SDK's one gap in that ownership:
  its live-task registry is keyed by task id alone, so ``tasks/cancel`` and
  ``tasks/subscribe`` on a *running* task would otherwise reach another
  owner's task;
* ``text_as_caller`` (called by the executor on the joined message text)
  drops every caller-written ``<source>`` envelope at the head of the message
  and puts the key's own in front.

The A2A session token is the trusted internal credential: its calls carry no
key, none of this applies, and the ``<source>`` it sends is used as given.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
import re
import uuid

from a2a.auth.user import User
from a2a.server.agent_execution import RequestContext
from a2a.server.agent_execution.simple_request_context_builder import (
    SimpleRequestContextBuilder,
)
from a2a.server.context import ServerCallContext
from a2a.server.events import Event
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes.common import DefaultServerCallContextBuilder
from a2a.types import CancelTaskRequest, SendMessageRequest, SubscribeToTaskRequest, Task
from a2a.utils.errors import InvalidParamsError, TaskNotFoundError
from starlette.requests import Request

from .mcp_keys import McpKeyRecord

# Request-state attribute the auth middleware sets when a request authenticated
# with a personal key (see ``a2a_server.create_a2a_app``).
PERSONAL_KEY_STATE_ATTR = "personal_key"
# The same record, carried on the A2A ServerCallContext.
_CALL_STATE_KEY = "personal_key"

_LEADING_SOURCE_RE = re.compile(r"^\s*<source>.*?</source>\s*", re.DOTALL)


class KeyUser(User):
    """A2A owner identity for one personal key: its tasks are its own."""

    def __init__(self, owner: str):
        self._owner = owner

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._owner


def request_key(request: Request) -> McpKeyRecord | None:
    """The personal key ``request`` authenticated with, or ``None`` for the A2A token."""
    state = getattr(request, "state", None)
    return getattr(state, PERSONAL_KEY_STATE_ATTR, None) if state is not None else None


def call_context_for(key: McpKeyRecord | None, **fields) -> ServerCallContext:
    """A2A call context for a key caller, or the shared one for the A2A token."""
    if key is None:
        return ServerCallContext(**fields)
    state = dict(fields.pop("state", None) or {})
    state[_CALL_STATE_KEY] = key
    return ServerCallContext(user=KeyUser(key.owner), state=state, **fields)


def call_context_key(context: ServerCallContext | None) -> McpKeyRecord | None:
    if context is None:
        return None
    key = context.state.get(_CALL_STATE_KEY)
    return key if isinstance(key, McpKeyRecord) else None


def strip_leading_sources(text: str) -> str:
    """Remove every ``<source>…</source>`` envelope at the head of ``text``."""
    while True:
        stripped = _LEADING_SOURCE_RE.sub("", text, count=1)
        if stripped == text:
            return text
        text = stripped


def apply_key_identity(text: str, key: McpKeyRecord) -> str:
    """``text`` as the agent must see it from ``key``: the key's identity, then the message."""
    body = strip_leading_sources(text)
    return f"{key.source_envelope()}\n{body}" if body else key.source_envelope()


def text_as_caller(text: str, context: ServerCallContext | None) -> str:
    """The message text as the agent must see it from this call's caller."""
    key = call_context_key(context)
    return text if key is None else apply_key_identity(text, key)


class PersonalKeyCallContextBuilder(DefaultServerCallContextBuilder):
    """Call contexts for the A2A routes: a key caller is its own task owner."""

    def build(self, request: Request) -> ServerCallContext:
        context = super().build(request)
        key = request_key(request)
        if key is None:
            return context
        return call_context_for(
            key,
            state=context.state,
            requested_extensions=context.requested_extensions,
        )


class KeyScopedRequestContextBuilder(SimpleRequestContextBuilder):
    """Keeps every personal key inside its own ``mk-<id>-`` session namespace."""

    async def build(
        self,
        context: ServerCallContext,
        params: SendMessageRequest | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
        task: Task | None = None,
    ) -> RequestContext:
        key = call_context_key(context)
        if key is not None:
            context_id = await self._key_context_id(key, context, task_id, context_id)
        return await super().build(
            context, params=params, task_id=task_id, context_id=context_id, task=task
        )

    async def _key_context_id(
        self,
        key: McpKeyRecord,
        context: ServerCallContext,
        task_id: str | None,
        context_id: str | None,
    ) -> str:
        if not context_id and task_id and self._task_store is not None:
            # Continuing one of the key's own tasks (the handler already
            # fetched it under this owner): stay in that task's session.
            existing = await self._task_store.get(task_id, context)
            context_id = existing.context_id if existing is not None else None
        if not context_id:
            return f"{key.session_prefix}{uuid.uuid4().hex}"
        if not context_id.startswith(key.session_prefix):
            # "not found" rather than confirm another caller's session exists.
            raise InvalidParamsError(message=f"session {context_id} not found")
        return context_id


class OwnerScopedRequestHandler(DefaultRequestHandler):
    """``DefaultRequestHandler`` whose cancel and subscribe respect task ownership."""

    async def _require_owned(self, task_id: str, context: ServerCallContext) -> None:
        if await self.task_store.get(task_id, context) is None:
            raise TaskNotFoundError

    async def on_cancel_task(
        self, params: CancelTaskRequest, context: ServerCallContext
    ) -> Task | None:
        await self._require_owned(params.id, context)
        return await super().on_cancel_task(params, context)

    async def on_subscribe_to_task(
        self, params: SubscribeToTaskRequest, context: ServerCallContext
    ) -> AsyncGenerator[Event, None]:
        await self._require_owned(params.id, context)
        async for event in super().on_subscribe_to_task(params, context):
            yield event
