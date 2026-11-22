"""The live agent running the current tool call (Aegis fork seam for agent-runtime plugin tools).

The registry hands a tool handler ids (task, session, conversation), never the ``AIAgent``. A plugin
tool that must act on the running agent itself (``aegis_a2a_delegate``: gateway-bound foreground
I/O, caller identity, activity heartbeat, cancel handle) reads it here instead of being wired into
the agent loop by name. ``agent/tool_executor.py`` binds it, together with the caller's user-env
identity, around every tool call on the thread that runs the call, so the sequential, concurrent
and helper paths all see it. Outside a tool call (and in a host-isolated plugin process) it is None.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Callable, TypeVar

from tools.user_env_runtime import call_as_agent_user

T = TypeVar("T")
R = TypeVar("R")

_TOOL_CALL_AGENT: ContextVar[Any] = ContextVar("hermes_tool_call_agent", default=None)


def current_tool_call_agent() -> Any:
    """The agent whose tool call is running on this thread, else None."""
    return _TOOL_CALL_AGENT.get()


def call_as_tool_agent(agent: Any, execute: Callable[[T], R], args: T) -> R:
    """Run ``execute(args)`` as *agent*'s tool call: bind the agent and its platform user, then restore."""
    token = _TOOL_CALL_AGENT.set(agent)
    try:
        return call_as_agent_user(agent, execute, args)
    finally:
        _TOOL_CALL_AGENT.reset(token)
