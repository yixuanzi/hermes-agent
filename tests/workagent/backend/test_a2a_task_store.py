"""Lifecycle bounds for the WORKAGENT A2A in-memory task store."""

from __future__ import annotations

import pytest

from a2a.server.context import ServerCallContext
from a2a.types import TaskState
from a2a.types.a2a_pb2 import Task, TaskStatus

from workagent.backend.a2a_service.task_store import BoundedTaskStore


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _task(task_id: str, state=TaskState.TASK_STATE_COMPLETED) -> Task:
    return Task(id=task_id, context_id=f"ctx-{task_id}", status=TaskStatus(state=state))


async def _save_all(store: BoundedTaskStore, context: ServerCallContext, *tasks: Task) -> None:
    for task in tasks:
        await store.save(task, context)


@pytest.mark.asyncio
async def test_settled_tasks_are_capped_in_lru_order():
    store = BoundedTaskStore(max_size=2, ttl_secs=3600)
    context = ServerCallContext()

    await _save_all(store, context, _task("t1"), _task("t2"), _task("t3"))

    assert store.tracked_task_ids() == ("t2", "t3")
    assert await store.get("t1", context) is None
    assert (await store.get("t3", context)).id == "t3"


@pytest.mark.asyncio
async def test_resaving_a_task_refreshes_its_lru_position():
    store = BoundedTaskStore(max_size=2, ttl_secs=3600)
    context = ServerCallContext()

    await _save_all(store, context, _task("t1"), _task("t2"))
    await store.save(_task("t1"), context)
    await store.save(_task("t3"), context)

    assert store.tracked_task_ids() == ("t1", "t3")
    assert await store.get("t2", context) is None


@pytest.mark.asyncio
async def test_settled_tasks_expire_after_the_ttl():
    clock = _Clock()
    store = BoundedTaskStore(max_size=64, ttl_secs=100, time_fn=clock)
    context = ServerCallContext()

    await store.save(_task("t-cold"), context)
    clock.advance(101)
    await store.save(_task("t-warm"), context)

    assert store.tracked_task_ids() == ("t-warm",)
    assert await store.get("t-cold", context) is None


@pytest.mark.asyncio
async def test_parked_tasks_are_evictable_but_in_flight_tasks_are_not():
    clock = _Clock()
    store = BoundedTaskStore(max_size=64, ttl_secs=100, time_fn=clock)
    context = ServerCallContext()

    await store.save(_task("t-working", TaskState.TASK_STATE_WORKING), context)
    await store.save(_task("t-submitted", TaskState.TASK_STATE_SUBMITTED), context)
    await store.save(_task("t-input", TaskState.TASK_STATE_INPUT_REQUIRED), context)
    clock.advance(101)
    await store.save(_task("t-new"), context)

    assert store.tracked_task_ids() == ("t-working", "t-submitted", "t-new")
    assert (await store.get("t-working", context)).id == "t-working"
    assert await store.get("t-input", context) is None


@pytest.mark.asyncio
async def test_the_cap_yields_to_in_flight_tasks():
    store = BoundedTaskStore(max_size=1, ttl_secs=3600)
    context = ServerCallContext()

    await _save_all(
        store,
        context,
        _task("t-working-1", TaskState.TASK_STATE_WORKING),
        _task("t-working-2", TaskState.TASK_STATE_WORKING),
        _task("t-done"),
    )

    # Over the cap on purpose: shedding a live task would break the request
    # that is still driving it.
    assert store.tracked_task_ids() == ("t-working-1", "t-working-2", "t-done")


@pytest.mark.asyncio
async def test_a_task_that_settles_becomes_evictable():
    store = BoundedTaskStore(max_size=1, ttl_secs=3600)
    context = ServerCallContext()

    await store.save(_task("t1", TaskState.TASK_STATE_WORKING), context)
    await store.save(_task("t2"), context)
    assert store.tracked_task_ids() == ("t1", "t2")

    await store.save(_task("t1", TaskState.TASK_STATE_COMPLETED), context)

    assert store.tracked_task_ids() == ("t1",)
    assert await store.get("t2", context) is None


@pytest.mark.asyncio
async def test_explicit_delete_stops_tracking_the_task():
    store = BoundedTaskStore(max_size=8, ttl_secs=3600)
    context = ServerCallContext()

    await store.save(_task("t1"), context)
    await store.delete("t1", context)

    assert store.tracked_task_ids() == ()
    assert await store.get("t1", context) is None


@pytest.mark.asyncio
async def test_bounds_default_to_the_shared_agent_cache_block(monkeypatch: pytest.MonkeyPatch):
    import workagent.backend.a2a_service.task_store as task_store_module

    monkeypatch.setattr(task_store_module, "resolve_cache_bounds", lambda: (5, 50.0))
    store = BoundedTaskStore()

    assert store.max_size == 5
    assert store.ttl_secs == 50.0


@pytest.mark.asyncio
async def test_list_and_get_still_delegate_to_the_inner_store():
    from a2a.types.a2a_pb2 import ListTasksRequest

    store = BoundedTaskStore(max_size=8, ttl_secs=3600)
    context = ServerCallContext()
    await _save_all(store, context, _task("t1"), _task("t2"))

    listed = await store.list(ListTasksRequest(), context)

    assert {task.id for task in listed.tasks} == {"t1", "t2"}
    assert (await store.get("t1", context)).context_id == "ctx-t1"
