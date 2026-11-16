"""Run an agent's turns on a codex app-server session its owner attached, keeping the host runtime.

A plugin backend (ext-tools ``/codex`` and ``/claude``) hands whole turns to a session it attached with
``agent.codex_runtime.attach_codex_app_server_session``. The turn path picks that session by
``api_mode == "codex_app_server"``, but the agent's real provider stays what it was: auxiliary calls
(titles, background review forks, compression feasibility) must keep routing by the host ``api_mode``.
Otherwise the codex_app_server -> codex_responses downgrade, meant for the native openai-codex runtime,
sends a chat_completions or anthropic_messages provider down the wrong protocol.
"""
from __future__ import annotations

from typing import Any

CODEX_APP_SERVER = "codex_app_server"
# Instance attribute holding the api_mode the agent had before its owner switched it.
_HOST_API_MODE = "_codex_owner_host_api_mode"


def host_api_mode(agent: Any) -> str | None:
    """The host ``api_mode`` while an owner runs the agent on its session, else None."""
    return getattr(agent, "__dict__", {}).get(_HOST_API_MODE)


def main_runtime_api_mode(agent: Any) -> str:
    """The ``api_mode`` auxiliary routing should see: the host's while an owner session runs the turns."""
    api_mode = getattr(agent, "api_mode", "") or ""
    host = host_api_mode(agent)
    return host if host is not None and api_mode == CODEX_APP_SERVER else api_mode


def enter_owner_codex_runtime(agent: Any) -> bool:
    """Route the agent's next turns to its owner-attached codex session. Call at a turn boundary.

    False (nothing switched) when codex_app_server already is the agent's own runtime. Re-entering
    refreshes the remembered host mode if it changed meanwhile (``/model``)."""
    current = getattr(agent, "api_mode", "") or ""
    if host_api_mode(agent) is None and current == CODEX_APP_SERVER:
        return False
    if current != CODEX_APP_SERVER:
        agent.__dict__[_HOST_API_MODE] = current
    agent.api_mode = CODEX_APP_SERVER
    _republish_main_runtime(agent)
    return True


def leave_owner_codex_runtime(agent: Any) -> bool:
    """Give the agent its host runtime back. False when no owner had switched it."""
    host = agent.__dict__.pop(_HOST_API_MODE, None)
    if host is None:
        return False
    if getattr(agent, "api_mode", None) == CODEX_APP_SERVER:
        agent.api_mode = host
    _republish_main_runtime(agent)
    return True


def _republish_main_runtime(agent: Any) -> None:
    # This turn already published its runtime before the owner's pre_llm_call switch ran.
    from agent.turn_context import _publish_runtime_main
    _publish_runtime_main(agent)
