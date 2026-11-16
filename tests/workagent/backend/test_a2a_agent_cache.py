"""Lifecycle bounds for the WORKAGENT A2A per-context agent cache."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from workagent.backend.agent_cache import (
    _DEFAULT_IDLE_TTL_SECS,
    _DEFAULT_MAX_SIZE,
    AgentCache,
    release_agent_soft,
    resolve_cache_bounds,
)
from workagent.backend.a2a_service.executor import HermesA2AExecutor


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeAgent:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.released = False
        self._session_messages = [{"role": "user", "content": "x"}]
        self._db_flush_scan_prefix = [{"role": "user", "content": "x"}]

    def release_clients(self) -> None:
        self.released = True


def _fake_config_module(payload: dict):
    return SimpleNamespace(load_config_readonly=lambda: payload)


def test_cache_reuses_the_agent_for_a_repeated_context():
    cache = AgentCache(max_size=8, idle_ttl_secs=3600)

    first, evicted_first = cache.get_or_create("ctx-1", _FakeAgent)
    second, evicted_second = cache.get_or_create("ctx-1", _FakeAgent)

    assert first is second
    assert evicted_first == [] and evicted_second == []
    assert len(cache) == 1


def test_cache_evicts_least_recently_used_over_the_cap():
    cache = AgentCache(max_size=2, idle_ttl_secs=3600)

    cache.get_or_create("ctx-1", _FakeAgent)
    cache.get_or_create("ctx-2", _FakeAgent)
    # Re-touch ctx-1 so ctx-2 becomes the least recently used entry.
    cache.get_or_create("ctx-1", _FakeAgent)
    _agent, evicted = cache.get_or_create("ctx-3", _FakeAgent)

    assert [key for key, _agent in evicted] == ["ctx-2"]
    assert sorted(cache.keys()) == ["ctx-1", "ctx-3"]


def test_cache_evicts_contexts_idle_past_the_ttl():
    clock = _Clock()
    cache = AgentCache(max_size=64, idle_ttl_secs=100, time_fn=clock)

    cache.get_or_create("ctx-cold", _FakeAgent)
    clock.advance(101)
    _agent, evicted = cache.get_or_create("ctx-warm", _FakeAgent)

    assert [key for key, _agent in evicted] == ["ctx-cold"]
    assert cache.keys() == ["ctx-warm"]


def test_touch_restarts_the_idle_clock_for_a_finished_turn():
    clock = _Clock()
    cache = AgentCache(max_size=64, idle_ttl_secs=100, time_fn=clock)

    cache.get_or_create("ctx-1", _FakeAgent)
    clock.advance(90)
    cache.touch("ctx-1")
    clock.advance(90)
    _agent, evicted = cache.get_or_create("ctx-2", _FakeAgent)

    assert evicted == []
    assert sorted(cache.keys()) == ["ctx-1", "ctx-2"]


def test_in_flight_contexts_are_never_evicted_even_over_the_cap():
    clock = _Clock()
    cache = AgentCache(max_size=1, idle_ttl_secs=10, time_fn=clock)

    running, _evicted = cache.get_or_create("ctx-running", _FakeAgent)
    clock.advance(1000)
    _agent, evicted = cache.get_or_create(
        "ctx-new", _FakeAgent, protected={"ctx-running"}
    )

    assert evicted == []
    assert cache.peek("ctx-running") is running
    assert len(cache) == 2


def test_peek_does_not_promote_a_cold_context():
    cache = AgentCache(max_size=2, idle_ttl_secs=3600)

    cache.get_or_create("ctx-1", _FakeAgent)
    cache.get_or_create("ctx-2", _FakeAgent)
    assert cache.peek("ctx-1") is not None
    _agent, evicted = cache.get_or_create("ctx-3", _FakeAgent)

    assert [key for key, _agent in evicted] == ["ctx-1"]
    assert cache.peek("ctx-missing") is None


def test_discard_and_clear_hand_back_the_agents():
    cache = AgentCache(max_size=8, idle_ttl_secs=3600)
    first, _evicted = cache.get_or_create("ctx-1", _FakeAgent)
    cache.get_or_create("ctx-2", _FakeAgent)

    assert cache.discard("ctx-1") is first
    assert cache.discard("ctx-1") is None
    assert [key for key, _agent in cache.clear()] == ["ctx-2"]
    assert len(cache) == 0


def test_an_is_protected_entry_survives_both_the_ttl_and_the_cap():
    clock = _Clock()
    pinned = {"ctx-pinned"}
    cache = AgentCache(
        max_size=1,
        idle_ttl_secs=10,
        is_protected=lambda key, _value: key in pinned,
        time_fn=clock,
    )

    cache.get_or_create("ctx-pinned", _FakeAgent)
    clock.advance(1000)
    _agent, evicted = cache.get_or_create("ctx-new", _FakeAgent)

    assert evicted == []
    assert sorted(cache.keys()) == ["ctx-new", "ctx-pinned"]

    # Unpin it and the very next call sheds it — on its TTL, alongside the
    # cap eviction that call also triggers.
    pinned.clear()
    _agent, evicted = cache.get_or_create("ctx-newer", _FakeAgent)
    assert [key for key, _value in evicted] == ["ctx-pinned", "ctx-new"]
    assert cache.keys() == ["ctx-newer"]


def test_an_entry_whose_protection_cannot_be_evaluated_is_kept():
    def _boom(_key, _value):
        raise RuntimeError("cannot classify")

    clock = _Clock()
    cache = AgentCache(max_size=1, idle_ttl_secs=10, is_protected=_boom, time_fn=clock)

    cache.get_or_create("ctx-1", _FakeAgent)
    clock.advance(1000)
    _agent, evicted = cache.get_or_create("ctx-2", _FakeAgent)

    assert evicted == []
    assert len(cache) == 2


def test_non_string_keys_are_kept_intact():
    cache = AgentCache(max_size=2, idle_ttl_secs=3600)

    cache.get_or_create(("owner-a", "s1"), _FakeAgent)
    agent, _evicted = cache.get_or_create(("owner-b", "s1"), _FakeAgent)

    assert cache.peek(("owner-b", "s1")) is agent
    assert cache.peek(("owner-a", "s1")) is not agent
    assert cache.keys() == [("owner-a", "s1"), ("owner-b", "s1")]
    assert [key for key, _value in cache.items()] == cache.keys()


def test_release_agent_soft_drops_the_client_pool_and_transcript():
    agent = _FakeAgent("ctx-1")

    release_agent_soft(agent)

    assert agent.released is True
    assert agent._session_messages == []
    assert agent._db_flush_scan_prefix is None


def test_release_agent_soft_never_raises():
    class _Hostile:
        def release_clients(self):
            raise RuntimeError("boom")

    release_agent_soft(_Hostile())
    release_agent_soft(None)
    release_agent_soft(object())


def test_bounds_come_from_the_shared_agent_cache_config_block():
    config_module = _fake_config_module(
        {"agent": {"agent_cache": {"max_size": 7, "idle_ttl_secs": 42}}}
    )

    assert resolve_cache_bounds(config_module) == (7, 42.0)

    cache = AgentCache(config_module=config_module)
    assert cache.max_size == 7
    assert cache.idle_ttl_secs == 42.0


def test_bounds_fall_back_to_defaults_when_the_config_read_fails():
    def _boom():
        raise RuntimeError("config unreadable")

    config_module = SimpleNamespace(load_config_readonly=_boom)

    assert resolve_cache_bounds(config_module) == (
        _DEFAULT_MAX_SIZE,
        _DEFAULT_IDLE_TTL_SECS,
    )

    cache = AgentCache(config_module=config_module)
    assert cache.max_size == _DEFAULT_MAX_SIZE
    assert cache.idle_ttl_secs == _DEFAULT_IDLE_TTL_SECS


@pytest.mark.asyncio
async def test_executor_bounds_the_agent_cache_and_releases_evicted_agents():
    built: list[_FakeAgent] = []

    def _factory(session_id: str) -> _FakeAgent:
        agent = _FakeAgent(session_id)
        built.append(agent)
        return agent

    executor = HermesA2AExecutor(
        agent_factory=_factory,
        agent_cache=AgentCache(max_size=2, idle_ttl_secs=3600),
    )

    for index in range(5):
        await executor._get_agent(f"ctx-{index}", task_id=f"task-{index}")
        # The turn ends: drop the in-flight protection the task id carries.
        executor._task_agent_keys.pop(f"task-{index}", None)

    assert len(executor._agents) == 2
    assert executor._agents.keys() == ["ctx-3", "ctx-4"]
    assert [agent.released for agent in built] == [True, True, True, False, False]
    assert built[0]._session_messages == []


@pytest.mark.asyncio
async def test_executor_keeps_an_agent_whose_turn_is_still_running():
    executor = HermesA2AExecutor(
        agent_factory=_FakeAgent,
        agent_cache=AgentCache(max_size=1, idle_ttl_secs=3600),
    )

    running = await executor._get_agent("ctx-running", task_id="task-running")
    # No pop: task-running is still in flight while the next turn starts.
    await executor._get_agent("ctx-next", task_id="task-next")

    assert executor._agents.peek("ctx-running") is running
    assert running.released is False
    assert sorted(executor._agents.keys()) == ["ctx-next", "ctx-running"]
