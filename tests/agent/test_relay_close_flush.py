"""Tests for thread-aware subscriber flush on Relay session close.

Covers the P1 fix for ``subscribers.flush()`` being rejected on asyncio
event-loop threads (see hand-off: fix/relay-close-flush-async).
"""

import asyncio
import logging
import threading
from types import SimpleNamespace

import pytest

from agent.relay_runtime import RelayRuntime


class RecordingSubscribers:
    """Test double recording sync/async flush calls."""

    def __init__(
        self,
        *,
        with_async: bool = True,
        flush_error: Exception | None = None,
        async_error: Exception | None = None,
    ) -> None:
        self.sync_calls = 0
        self.async_calls = 0
        self._with_async = with_async
        self._flush_error = flush_error
        self._async_error = async_error

    def flush(self) -> None:
        self.sync_calls += 1
        if self._flush_error is not None:
            raise self._flush_error

    async def flush_async(self) -> None:
        self.async_calls += 1
        await asyncio.sleep(0)
        if self._async_error is not None:
            raise self._async_error

    def has_flush_async(self) -> bool:
        return self._with_async


class RecordingSubscribersNoAsync:
    """Test double simulating an old library without flush_async."""

    def __init__(self, **_kwargs) -> None:
        self.sync_calls = 0
        self.async_calls = 0

    def flush(self) -> None:
        self.sync_calls += 1


class RecordingScope:
    """Minimal scope stand-in: push/pop track handles without real Relay."""

    def __init__(self) -> None:
        self._next = 0

    def push(self, *args, **kwargs):
        self._next += 1
        return f"handle-{self._next}"

    def pop(self, *args, **kwargs):
        return {}


def _make_runtime(with_async: bool = True, **sub_kwargs) -> RelayRuntime:
    if with_async:
        subscribers = RecordingSubscribers(**sub_kwargs)
    else:
        subscribers = RecordingSubscribersNoAsync(**sub_kwargs)
    relay = SimpleNamespace(
        scope=RecordingScope(),
        ScopeType=SimpleNamespace(Agent="agent"),
        get_scope_stack=lambda: [],
        subscribers=subscribers,
    )
    runtime = RelayRuntime(relay=relay)
    # Register a session directly so close_session has something to close.
    session = runtime.ensure_session({"session_id": "sess-1"})
    assert session is not None
    return runtime


def test_close_session_sync_thread_uses_sync_flush(caplog):
    """No running loop: flush() called once, no warning, session removed."""
    runtime = _make_runtime()
    with caplog.at_level(logging.WARNING, logger="agent.relay_runtime"):
        runtime.close_session({"session_id": "sess-1"})
    subs = runtime.relay.subscribers
    assert subs.sync_calls == 1
    assert subs.async_calls == 0
    assert "closed with errors" not in caplog.text
    assert "sess-1" not in runtime._sessions


@pytest.mark.asyncio
async def test_close_session_loop_thread_schedules_flush_async(caplog):
    """Event-loop thread: flush() NOT called; flush_async scheduled and done."""
    runtime = _make_runtime()
    with caplog.at_level(logging.WARNING, logger="agent.relay_runtime"):
        runtime.close_session({"session_id": "sess-1"})
        # Let the scheduled flush task run to completion.
        await asyncio.sleep(0.05)
    subs = runtime.relay.subscribers
    assert subs.sync_calls == 0
    assert subs.async_calls == 1
    assert "closed with errors" not in caplog.text
    assert "sess-1" not in runtime._sessions


@pytest.mark.asyncio
async def test_close_session_loop_thread_async_flush_failure_warns(caplog):
    """flush_async() raising surfaces 'deferred subscriber flush failed'."""
    runtime = _make_runtime(async_error=RuntimeError("boom"))
    with caplog.at_level(logging.WARNING, logger="agent.relay_runtime"):
        runtime.close_session({"session_id": "sess-1"})
        await asyncio.sleep(0.05)
    assert "deferred subscriber flush failed" in caplog.text
    assert "Task exception was never retrieved" not in caplog.text


@pytest.mark.asyncio
async def test_close_session_loop_thread_without_flush_async_is_silent(caplog):
    """Old library / test double without flush_async: skip without warning."""
    runtime = _make_runtime(with_async=False)
    with caplog.at_level(logging.WARNING, logger="agent.relay_runtime"):
        runtime.close_session({"session_id": "sess-1"})
    subs = runtime.relay.subscribers
    assert subs.sync_calls == 0
    assert "closed with errors" not in caplog.text
    assert "sess-1" not in runtime._sessions


def test_close_session_sync_thread_flush_failure_records_error(caplog):
    """Sync path real failure still recorded via 'closed with errors'."""
    runtime = _make_runtime(flush_error=RuntimeError("sync barrier boom"))
    with caplog.at_level(logging.WARNING, logger="agent.relay_runtime"):
        runtime.close_session({"session_id": "sess-1"})
    assert "subscriber flush failed" in caplog.text
    assert "closed with errors" in caplog.text


def test_shutdown_on_worker_thread_flushes_synchronously():
    """shutdown() on a loop-free thread keeps the synchronous barrier."""
    runtime = _make_runtime()
    results: dict[str, int] = {}

    def _run() -> None:
        runtime.shutdown()
        subs = runtime.relay.subscribers
        results["sync"] = subs.sync_calls
        results["async"] = subs.async_calls

    thread = threading.Thread(target=_run)
    thread.start()
    thread.join(timeout=10)
    assert results == {"sync": 1, "async": 0}


def test_plugin_flush_helper_sync_thread():
    """Plugin helper: sync thread uses flush()."""
    import importlib

    plugin = importlib.import_module("plugins.observability.nemo_relay")
    subs = RecordingSubscribers()
    nemo = SimpleNamespace(subscribers=subs)
    plugin._flush_relay_subscribers(nemo)
    assert subs.sync_calls == 1
    assert subs.async_calls == 0


@pytest.mark.asyncio
async def test_plugin_flush_helper_loop_thread():
    """Plugin helper: event-loop thread schedules flush_async, not flush()."""
    import importlib

    plugin = importlib.import_module("plugins.observability.nemo_relay")
    subs = RecordingSubscribers()
    nemo = SimpleNamespace(subscribers=subs)
    plugin._flush_relay_subscribers(nemo)
    await asyncio.sleep(0.05)
    assert subs.sync_calls == 0
    assert subs.async_calls == 1
