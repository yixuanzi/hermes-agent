"""Tests for thread-aware subscriber flush on Relay session close.

Covers the P1 fix for ``subscribers.flush()`` being rejected on asyncio
event-loop threads (see hand-off: fix/relay-close-flush-async).
"""

import asyncio
import gc
import importlib
import logging
import threading
from types import SimpleNamespace

import pytest

from agent.relay_runtime import RelayRuntime


def _loop_running_on_this_thread() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class RecordingSubscribers:
    """Test double that keeps nemo-relay's flush()/flush_async() contract.

    Like nemo-relay 0.7.1, ``flush()`` refuses to block a running event loop,
    so a regression back to the sync barrier on a loop thread fails loudly.
    """

    def __init__(
        self,
        *,
        flush_error: Exception | None = None,
        async_error: Exception | None = None,
    ) -> None:
        self.sync_calls = 0
        self.async_calls = 0
        self.async_completed = 0
        self._flush_error = flush_error
        self._async_error = async_error

    def flush(self) -> None:
        if _loop_running_on_this_thread():
            raise RuntimeError(
                "subscribers.flush() cannot block a running asyncio event loop"
            )
        self.sync_calls += 1
        if self._flush_error is not None:
            raise self._flush_error

    async def flush_async(self) -> None:
        self.async_calls += 1
        await asyncio.sleep(0)
        if self._async_error is not None:
            raise self._async_error
        self.async_completed += 1


class RecordingSubscribersNoAsync:
    """Test double simulating an old library without flush_async."""

    def __init__(self) -> None:
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
        subscribers = RecordingSubscribersNoAsync()
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
        assert len(runtime._pending_flushes) == 1
        # Let the scheduled flush task run to completion.
        await asyncio.sleep(0.05)
    subs = runtime.relay.subscribers
    assert subs.sync_calls == 0
    assert subs.async_completed == 1
    assert runtime._pending_flushes == set()
    assert "closed with errors" not in caplog.text
    assert "sess-1" not in runtime._sessions


@pytest.mark.asyncio
async def test_close_session_loop_thread_async_flush_failure_warns(caplog):
    """flush_async() raising is logged with the session id and retrieved."""
    loop = asyncio.get_running_loop()
    unhandled: list[dict] = []
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        runtime = _make_runtime(async_error=RuntimeError("boom"))
        with caplog.at_level(logging.WARNING, logger="agent.relay_runtime"):
            runtime.close_session({"session_id": "sess-1"})
            await asyncio.sleep(0.05)
        # A task whose exception was never retrieved reports it to the loop's
        # exception handler when it is garbage-collected.
        gc.collect()
    finally:
        loop.set_exception_handler(None)
    assert (
        "Hermes Relay session sess-1 deferred subscriber flush failed: boom"
        in caplog.text
    )
    assert unhandled == []


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
    # Detach from atexit so the failing double is not flushed at process exit.
    runtime.relay.subscribers._flush_error = None
    runtime.shutdown()


def test_shutdown_on_worker_thread_flushes_synchronously():
    """shutdown() on a loop-free thread uses only the synchronous barrier."""
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
    # One barrier from closing sess-1, one final barrier from shutdown().
    assert results == {"sync": 2, "async": 0}
    assert runtime._sessions == {}


def test_shutdown_flushes_after_loop_teardown_cancelled_deferred_flush():
    """A deferred flush lost with its loop is covered by shutdown's barrier."""
    runtime = _make_runtime()
    subs = runtime.relay.subscribers

    async def _close_on_loop() -> None:
        runtime.close_session({"session_id": "sess-1"})

    # asyncio.run cancels the still-pending flush_async task on teardown.
    asyncio.run(_close_on_loop())
    assert subs.sync_calls == 0
    assert subs.async_completed == 0
    assert runtime._pending_flushes == set()

    runtime.shutdown()
    assert subs.sync_calls == 1


@pytest.mark.asyncio
async def test_shutdown_on_loop_thread_never_blocks(caplog):
    """shutdown() on a loop thread skips the sync barrier and defers flushes."""
    runtime = _make_runtime()
    with caplog.at_level(logging.WARNING, logger="agent.relay_runtime"):
        runtime.shutdown()
        await asyncio.sleep(0.05)
    subs = runtime.relay.subscribers
    assert subs.sync_calls == 0
    assert subs.async_calls == 1
    assert runtime._sessions == {}
    assert caplog.text == ""


def test_plugin_flush_helper_sync_thread():
    """Plugin helper: sync thread uses flush()."""
    plugin = importlib.import_module("plugins.observability.nemo_relay")
    subs = RecordingSubscribers()
    nemo = SimpleNamespace(subscribers=subs)
    plugin._flush_relay_subscribers(nemo)
    assert subs.sync_calls == 1
    assert subs.async_calls == 0


@pytest.mark.asyncio
async def test_plugin_flush_helper_loop_thread():
    """Plugin helper: event-loop thread schedules flush_async, not flush()."""
    plugin = importlib.import_module("plugins.observability.nemo_relay")
    subs = RecordingSubscribers()
    nemo = SimpleNamespace(subscribers=subs)
    plugin._flush_relay_subscribers(nemo)
    await asyncio.sleep(0.05)
    assert subs.sync_calls == 0
    assert subs.async_calls == 1


@pytest.mark.asyncio
async def test_plugin_release_on_loop_thread_defers_flush_and_closes(caplog):
    """release() on a loop thread neither raises nor skips activation close."""
    plugin = importlib.import_module("plugins.observability.nemo_relay")
    configuration = plugin._ProcessPluginConfiguration()
    owner = object()
    closed: list[bool] = []
    configuration._owners.add(id(owner))
    configuration._activation = SimpleNamespace(close=lambda: closed.append(True))
    subs = RecordingSubscribers()

    with caplog.at_level(logging.WARNING):
        configuration.release(owner, SimpleNamespace(subscribers=subs))
        await asyncio.sleep(0.05)

    assert closed == [True]
    assert subs.sync_calls == 0
    assert subs.async_calls == 1
    assert configuration._activation is None
    assert caplog.text == ""


@pytest.mark.asyncio
async def test_plugin_release_logs_deferred_flush_failure(caplog):
    """A failing deferred flush in release() surfaces as a plugin warning."""
    plugin = importlib.import_module("plugins.observability.nemo_relay")
    configuration = plugin._ProcessPluginConfiguration()
    owner = object()
    configuration._owners.add(id(owner))
    configuration._activation = SimpleNamespace(close=lambda: None)
    subs = RecordingSubscribers(async_error=RuntimeError("boom"))

    with caplog.at_level(logging.WARNING):
        configuration.release(owner, SimpleNamespace(subscribers=subs))
        await asyncio.sleep(0.05)

    assert "NeMo Relay plugin deferred subscriber flush failed: boom" in caplog.text
