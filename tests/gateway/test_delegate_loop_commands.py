"""Gateway commands while an a2a_delegate foreground loop owns the chat."""

import threading

import pytest

from gateway.config import Platform
from gateway.platforms.base import is_gateway_command_during_delegate_loop
from gateway.session import SessionSource


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/stop", True),
        ("  /STOP  ", True),
        ("/new", True),
        ("/stop@hermes_bot", True),
        ("/main", False),
        ("/exit", False),
        ("/exit now", False),
        ("follow up", False),
        ("", False),
        ("/no-such-command", False),
        ("/tmp/report.txt", False),
    ],
)
def test_only_resolvable_gateway_commands_skip_the_loop(text, expected):
    assert is_gateway_command_during_delegate_loop(text) is expected


@pytest.mark.asyncio
async def test_interrupt_and_clear_session_cancels_the_running_a2a_delegation(monkeypatch):
    import tools.a2a_delegate_tool as a2a_delegate_tool
    from gateway.run import GatewayRunner, _INTERRUPT_REASON_STOP

    class _RecordingAgent:
        def __init__(self):
            self.interrupt_reasons = []

        def interrupt(self, reason=None):
            self.interrupt_reasons.append(reason)

    cancelled = []
    cancel_called = threading.Event()

    def _record_cancel(parent_agent):
        cancelled.append(parent_agent)
        cancel_called.set()
        return "completed"

    monkeypatch.setattr(a2a_delegate_tool, "cancel_active_a2a_delegate", _record_cancel)

    agent = _RecordingAgent()
    session_key = "agent:main:feishu:group:oc_chat"
    source = SessionSource(platform=Platform.FEISHU, chat_id="oc_chat", chat_type="group")
    runner = object.__new__(GatewayRunner)
    runner._running_agents = {session_key: agent}
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    runner.adapters = {}
    runner._pending_messages = {}
    runner._invalidate_session_run_generation = lambda key, reason=None: 1
    runner._release_running_agent_state = lambda key, **kw: None

    await runner._interrupt_and_clear_session(
        session_key,
        source,
        interrupt_reason=_INTERRUPT_REASON_STOP,
        invalidation_reason="stop_command",
    )

    assert agent.interrupt_reasons == [_INTERRUPT_REASON_STOP]
    assert cancel_called.wait(1)
    assert cancelled == [agent]
