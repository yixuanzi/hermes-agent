"""Gateway commands while an aegis_a2a_delegate foreground loop owns the chat."""

import threading

import pytest

from tools import a2a_delegate_tool
from gateway.platforms.base_delegate_loop import is_gateway_command_during_delegate_loop
from gateway.run import GatewayRunner, _INTERRUPT_REASON_STOP
from gateway.session_state import SessionState


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


def test_interrupting_a_running_turn_cancels_its_a2a_delegation(monkeypatch):
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
    runner = object.__new__(GatewayRunner)
    runner._sessions = {session_key: SessionState()}
    runner._sessions[session_key].turn.agent = agent

    runner._interrupt_running_turn(
        session_key, interrupt_reason=_INTERRUPT_REASON_STOP, invalidation_reason="stop_command")

    assert agent.interrupt_reasons == [_INTERRUPT_REASON_STOP]
    assert cancel_called.wait(5)
    assert cancelled == [agent]
