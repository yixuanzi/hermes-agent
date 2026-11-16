"""Lifecycle bounds for the Aegis chat session actor cache."""

from __future__ import annotations

import pytest

from workagent.backend.agent_cache import AgentCache


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

    def release_clients(self) -> None:
        self.released = True


def _factory(session_id: str, **_kwargs) -> _FakeAgent:
    return _FakeAgent(session_id)


def _manager(service, *, max_size: int, idle_ttl_secs: float, time_fn=None):
    cache = AgentCache(
        max_size=max_size,
        idle_ttl_secs=idle_ttl_secs,
        is_protected=service._session_is_protected,
        **({"time_fn": time_fn} if time_fn is not None else {}),
    )
    return service.ChatSessionManager(_factory, session_cache=cache)


def _bind(manager, session_id: str):
    websocket = object()
    actor = manager.bind(
        websocket=websocket,
        loop=object(),
        session_id=session_id,
        title=session_id,
    )
    return actor, websocket


@pytest.fixture
def service(load_backend, hermes_home):
    return load_backend("aegis.backend.chat.service")


def test_disconnected_idle_sessions_are_shed_over_the_cap(service):
    manager = _manager(service, max_size=2, idle_ttl_secs=3600)

    first, first_ws = _bind(manager, "s1")
    manager.release_connection(first, first_ws)
    second, second_ws = _bind(manager, "s2")
    manager.release_connection(second, second_ws)
    _bind(manager, "s3")

    assert manager.get("s1") is None
    assert manager.get("s2") is not None
    assert manager.get("s3") is not None
    assert first._agent.released is True
    assert first._agent._session_messages == []


def test_a_bound_session_is_never_evicted(service):
    manager = _manager(service, max_size=1, idle_ttl_secs=1)

    bound, _ws = _bind(manager, "s-bound")
    idle, idle_ws = _bind(manager, "s-idle")
    manager.release_connection(idle, idle_ws)
    _bind(manager, "s-new")

    # s-bound still has a websocket, so it stays over the cap; s-idle is the
    # one that goes.
    assert manager.get("s-bound") is bound
    assert bound._agent.released is False
    assert manager.get("s-idle") is None


def test_a_session_running_a_turn_is_never_evicted(service):
    manager = _manager(service, max_size=1, idle_ttl_secs=3600)

    busy, busy_ws = _bind(manager, "s-busy")
    manager.release_connection(busy, busy_ws)

    class _AliveThread:
        def is_alive(self) -> bool:
            return True

    busy._running_thread = _AliveThread()
    _bind(manager, "s-other")

    assert manager.get("s-busy") is busy
    assert busy._agent.released is False


def test_a_session_waiting_on_the_user_is_never_evicted(service):
    manager = _manager(service, max_size=1, idle_ttl_secs=3600)

    waiting, waiting_ws = _bind(manager, "s-waiting")
    manager.release_connection(waiting, waiting_ws)
    waiting._pending_approval = object()
    _bind(manager, "s-other")

    assert manager.get("s-waiting") is waiting
    assert waiting._agent.released is False


def test_idle_sessions_expire_after_the_ttl(service):
    clock = _Clock()
    manager = _manager(service, max_size=64, idle_ttl_secs=100, time_fn=clock)

    cold, cold_ws = _bind(manager, "s-cold")
    manager.release_connection(cold, cold_ws)
    clock.advance(101)
    _bind(manager, "s-warm")

    assert manager.get("s-cold") is None
    assert manager.get("s-warm") is not None


def test_the_idle_clock_restarts_at_disconnect_not_at_bind(service):
    clock = _Clock()
    manager = _manager(service, max_size=64, idle_ttl_secs=100, time_fn=clock)

    long_lived, long_lived_ws = _bind(manager, "s-long")
    clock.advance(90)
    manager.release_connection(long_lived, long_lived_ws)
    clock.advance(90)
    _bind(manager, "s-other")

    assert manager.get("s-long") is long_lived


def test_rebinding_an_evicted_session_builds_a_fresh_actor(service):
    manager = _manager(service, max_size=1, idle_ttl_secs=3600)

    original, original_ws = _bind(manager, "s1")
    manager.release_connection(original, original_ws)
    _bind(manager, "s2")
    assert manager.get("s1") is None

    rebound, _ws = _bind(manager, "s1")

    assert rebound is not original
    assert rebound.session_id == "s1"


def test_rebinding_a_cached_session_reuses_its_actor(service):
    manager = _manager(service, max_size=8, idle_ttl_secs=3600)

    first, first_ws = _bind(manager, "s1")
    manager.release_connection(first, first_ws)
    again, _ws = _bind(manager, "s1")

    assert again is first
    assert first._agent.released is False


def test_rebinding_the_same_socket_frees_the_session_it_left(service):
    manager = _manager(service, max_size=8, idle_ttl_secs=3600)

    websocket = object()
    first = manager.bind(
        websocket=websocket, loop=object(), session_id="s1", title="s1"
    )
    second = manager.bind(
        websocket=websocket, loop=object(), session_id="s2", title="s2"
    )

    # The client moved to s2, so s1 must no longer read as attached — otherwise
    # it stays protected from eviction for the life of the process.
    assert first.holds_websocket(websocket) is False
    assert second.holds_websocket(websocket) is True
    assert first.is_evictable() is True
    assert second.is_evictable() is False


def test_swapping_the_agent_factory_releases_every_cached_actor(service):
    manager = _manager(service, max_size=8, idle_ttl_secs=3600)

    actor, actor_ws = _bind(manager, "s1")
    manager.release_connection(actor, actor_ws)
    manager.set_agent_factory(_factory)

    assert manager.get("s1") is None
    assert actor._agent.released is True
