"""Bounded in-memory A2A task store for the WORKAGENT A2A server.

The SDK's ``InMemoryTaskStore`` is a nested ``owner -> task_id -> Task`` dict
that only ever drops a task when a client explicitly calls ``tasks/delete``.
Nothing in the A2A flow does: a delegation sends a message, polls until the
task reaches a terminal state, and walks away.  Every finished task — plus the
deep copy ``CopyingTaskStoreAdapter`` keeps of it — therefore stays resident
for the life of the process, so a long-lived A2A server accumulates one record
per request forever.

This wrapper adds the lifecycle the store is missing: an LRU entry cap and an
idle TTL, per owner partition, applied on every ``save``.

Only *settled* tasks are evictable.  A task in ``submitted`` or ``working``
belongs to a request that is still in flight, and dropping it would make a
concurrent ``tasks/get`` or ``tasks/cancel`` fail with "not found" mid-turn, so
those are protected even when that keeps the store over its cap — the same
in-flight protection the agent cache applies.  The trade-off is deliberate: a
task wedged in ``working`` by an abnormally dead turn pins one protobuf record
(kilobytes), while the growth this bounds is the unbounded stream of finished
ones.  Tasks parked in ``input_required`` / ``auth_required`` *are* evictable —
a client that never came back to answer is exactly what the TTL is for.

Bounds come from the shared ``agent.agent_cache`` block (see
``workagent.backend.agent_cache``), so one setting governs the agent cache and
this store.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
import logging
import time

from a2a.server.context import ServerCallContext
from a2a.server.owner_resolver import OwnerResolver, resolve_user_scope
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.tasks.task_store import TaskStore
from a2a.types import TaskState
from a2a.types.a2a_pb2 import ListTasksRequest, ListTasksResponse, Task

from workagent.backend.agent_cache import resolve_cache_bounds


logger = logging.getLogger(__name__)


# States that mean "a request is still driving this task".  Everything else —
# the terminal states and the ones parked waiting on a client that may never
# return — may be evicted.  UNSPECIFIED is treated as live because a task we
# cannot classify is one we must not drop.
_LIVE_TASK_STATES = frozenset(
    {
        TaskState.TASK_STATE_UNSPECIFIED,
        TaskState.TASK_STATE_SUBMITTED,
        TaskState.TASK_STATE_WORKING,
    }
)


def _task_is_live(task: Task) -> bool:
    try:
        return task.status.state in _LIVE_TASK_STATES
    except Exception:
        # Fail closed, as above.
        return True


@dataclass(slots=True)
class _Record:
    last_saved: float
    live: bool


class BoundedTaskStore(TaskStore):
    """``InMemoryTaskStore`` with an LRU cap and an idle TTL per owner.

    Eviction runs inside ``save`` and only ever touches the owner partition of
    the caller doing the saving.  That keeps the implementation honest about
    the SDK's ownership model — deleting another owner's task would need a
    ``ServerCallContext`` that resolves to them, and stashing contexts just to
    manufacture one would pin the very auth objects they carry.  With the
    single-owner setup the WORKAGENT server runs (bearer-token middleware, no
    per-user A2A identity) that is simply the whole store.
    """

    def __init__(
        self,
        inner: TaskStore | None = None,
        *,
        max_size: int | None = None,
        ttl_secs: float | None = None,
        owner_resolver: OwnerResolver = resolve_user_scope,
        time_fn: Callable[[], float] = time.monotonic,
    ):
        self._inner = inner if inner is not None else InMemoryTaskStore(
            owner_resolver=owner_resolver
        )
        self._owner_resolver = owner_resolver
        self._time_fn = time_fn
        self._max_size = max_size
        self._ttl_secs = ttl_secs
        # owner -> task_id -> record, in LRU order within each owner.
        self._records: dict[str, OrderedDict[str, _Record]] = {}

    # ── bounds ──────────────────────────────────────────────────────────
    def _resolve_bounds(self) -> None:
        if self._max_size is not None and self._ttl_secs is not None:
            return
        max_size, ttl_secs = resolve_cache_bounds()
        if self._max_size is None:
            self._max_size = max_size
        if self._ttl_secs is None:
            self._ttl_secs = ttl_secs

    @property
    def max_size(self) -> int:
        self._resolve_bounds()
        return int(self._max_size or 0)

    @property
    def ttl_secs(self) -> float:
        self._resolve_bounds()
        return float(self._ttl_secs or 0.0)

    # ── TaskStore ───────────────────────────────────────────────────────
    async def save(self, task: Task, context: ServerCallContext) -> None:
        await self._inner.save(task, context)
        owner = self._resolve_owner(context)
        if owner is None:
            return
        partition = self._records.setdefault(owner, OrderedDict())
        partition[task.id] = _Record(last_saved=self._time_fn(), live=_task_is_live(task))
        partition.move_to_end(task.id)
        await self._evict(owner, context, keep_task_id=task.id)

    async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
        return await self._inner.get(task_id, context)

    async def list(
        self,
        params: ListTasksRequest,
        context: ServerCallContext,
    ) -> ListTasksResponse:
        return await self._inner.list(params, context)

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        await self._inner.delete(task_id, context)
        owner = self._resolve_owner(context)
        if owner is None:
            return
        self._forget(owner, task_id)

    # ── internals ───────────────────────────────────────────────────────
    def _resolve_owner(self, context: ServerCallContext) -> str | None:
        """Resolve the caller's owner scope, or ``None`` when it cannot be read.

        A bookkeeping failure must never fail the save that triggered it; the
        task is already stored, it just goes untracked by this wrapper.
        """
        try:
            return str(self._owner_resolver(context))
        except Exception as exc:
            logger.debug("A2A task store owner resolution failed: %s", exc)
            return None

    def _forget(self, owner: str, task_id: str) -> None:
        partition = self._records.get(owner)
        if partition is None:
            return
        partition.pop(task_id, None)
        if not partition:
            self._records.pop(owner, None)

    async def _evict(
        self,
        owner: str,
        context: ServerCallContext,
        *,
        keep_task_id: str | None = None,
    ) -> None:
        """Shed settled tasks for ``owner`` down to the cap and the TTL.

        ``keep_task_id`` is the task the caller just saved.  Evicting it inside
        its own ``save`` would make the write a no-op and 404 the very task the
        client is about to fetch, so it is protected the same way the agent
        cache protects the context it was just asked for.
        """
        partition = self._records.get(owner)
        if not partition:
            return

        now = self._time_fn()
        ttl_secs = self.ttl_secs
        max_size = self.max_size
        expired = [
            task_id
            for task_id, record in partition.items()
            if not record.live
            and task_id != keep_task_id
            and now - record.last_saved > ttl_secs
        ]

        overflow: list[str] = []
        remaining = len(partition) - len(expired)
        if remaining > max_size:
            dropped = set(expired)
            for task_id, record in partition.items():
                if remaining <= max_size:
                    break
                if record.live or task_id == keep_task_id or task_id in dropped:
                    continue
                overflow.append(task_id)
                remaining -= 1
            if remaining > max_size:
                logger.debug(
                    "A2A task store for owner %r is over its cap (%d > %d) with "
                    "every remaining task in flight",
                    owner,
                    remaining,
                    max_size,
                )

        for task_id in (*expired, *overflow):
            try:
                await self._inner.delete(task_id, context)
            except Exception as exc:
                logger.debug("A2A task store eviction of %s failed: %s", task_id, exc)
            self._forget(owner, task_id)

        if expired or overflow:
            logger.info(
                "A2A task store evicted %d task(s) for owner %r "
                "(%d expired, %d over cap; kept=%d, max=%d, ttl=%ss)",
                len(expired) + len(overflow),
                owner,
                len(expired),
                len(overflow),
                len(self._records.get(owner) or ()),
                max_size,
                ttl_secs,
            )

    # ── diagnostics ─────────────────────────────────────────────────────
    def tracked_task_ids(self, owner: str = "") -> tuple[str, ...]:
        """Task ids this wrapper is tracking for ``owner``, in LRU order.

        A tuple, not a list: this class defines a ``list`` method (the
        ``TaskStore`` interface), which shadows the builtin for annotations
        in the class body.
        """
        return tuple(self._records.get(owner) or ())
