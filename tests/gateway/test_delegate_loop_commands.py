"""Gateway commands while an aegis_a2a_delegate foreground loop owns the chat."""

import threading

import pytest

from plugins.aegis_a2a import delegate_tool as a2a_delegate_tool
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


def test_interrupting_a_running_turn_cancels_its_a2a_delegation():
    """The gateway reaches the aegis_a2a plugin's cancel handle on the agent, never importing the plugin."""

    class _RecordingAgent:
        def __init__(self):
            self.interrupt_reasons = []

        def interrupt(self, reason=None):
            self.interrupt_reasons.append(reason)

    class _IdleLoopSession:
        """A foreground loop waiting for input: cancelling closes its input."""

        def __init__(self):
            self.closed = threading.Event()

        def mark_stop_requested(self):
            pass

        def has_stoppable_work(self):
            return not self.closed.is_set()

        def remote_turn_running(self):
            return False

        def close_foreground_input(self):
            self.closed.set()
            return True

    agent = _RecordingAgent()
    session = _IdleLoopSession()
    a2a_delegate_tool._register_active_a2a_session(agent, session)
    session_key = "agent:main:feishu:group:oc_chat"
    runner = object.__new__(GatewayRunner)
    runner._sessions = {session_key: SessionState()}
    runner._sessions[session_key].turn.agent = agent

    runner._interrupt_running_turn(
        session_key, interrupt_reason=_INTERRUPT_REASON_STOP, invalidation_reason="stop_command")

    assert agent.interrupt_reasons == [_INTERRUPT_REASON_STOP]
    assert session.closed.wait(5)


def test_interrupting_a_turn_without_a_delegation_cancels_nothing():
    class _Agent:
        def interrupt(self, reason=None):
            pass

    session_key = "agent:main:feishu:group:oc_chat"
    runner = object.__new__(GatewayRunner)
    runner._sessions = {session_key: SessionState()}
    runner._sessions[session_key].turn.agent = _Agent()

    before = {t.name for t in threading.enumerate()}
    runner._interrupt_running_turn(
        session_key, interrupt_reason=_INTERRUPT_REASON_STOP, invalidation_reason="stop_command")

    assert "gateway-a2a-delegate-cancel" not in {t.name for t in threading.enumerate()} - before
