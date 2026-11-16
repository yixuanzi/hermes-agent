"""An owner session runs the agent's turns while auxiliary routing keeps the host runtime.

A plugin backend (ext-tools ``/codex``, ``/claude``) switches the agent to ``codex_app_server`` at a turn
boundary through ``agent.codex_owner_runtime``. The main-runtime readers (``AIAgent._current_main_runtime``
and the per-turn auxiliary publish) must keep reporting the host api_mode, or the codex downgrade sends a
chat_completions / anthropic_messages provider down the wrong protocol.
"""
from __future__ import annotations

import pytest

from agent import auxiliary_client
from agent.codex_owner_runtime import (
    enter_owner_codex_runtime,
    host_api_mode,
    leave_owner_codex_runtime,
    main_runtime_api_mode,
)
from agent.codex_runtime import owner_session_approval
from agent.turn_context import _publish_runtime_main
from run_agent import AIAgent


@pytest.fixture
def agent():
    return AIAgent(api_key="test-key", base_url="https://openrouter.ai/api/v1", model="deepseek-v4-flash",
                   quiet_mode=True, skip_context_files=True, skip_memory=True, session_id="sess-owner")


def _published_api_mode(monkeypatch, agent) -> str:
    seen = {}
    monkeypatch.setattr(auxiliary_client, "set_runtime_main",
                        lambda *args, **kwargs: seen.update(kwargs))
    _publish_runtime_main(agent)
    return seen["api_mode"]


def test_owner_runtime_routes_turns_but_keeps_host_for_auxiliary_calls(agent, monkeypatch):
    host = agent.api_mode
    assert host != "codex_app_server"

    assert enter_owner_codex_runtime(agent) is True

    assert agent.api_mode == "codex_app_server"
    assert host_api_mode(agent) == host
    assert agent._current_main_runtime()["api_mode"] == host
    assert _published_api_mode(monkeypatch, agent) == host

    assert leave_owner_codex_runtime(agent) is True
    assert agent.api_mode == host
    assert host_api_mode(agent) is None
    assert main_runtime_api_mode(agent) == host


def test_reentering_follows_a_host_switch_made_in_between(agent):
    enter_owner_codex_runtime(agent)
    leave_owner_codex_runtime(agent)
    agent.api_mode = "anthropic_messages"  # /model moved the host to another protocol

    enter_owner_codex_runtime(agent)

    assert agent._current_main_runtime()["api_mode"] == "anthropic_messages"


def test_native_codex_runtime_is_not_taken_over(agent):
    agent.api_mode = "codex_app_server"

    assert enter_owner_codex_runtime(agent) is False
    assert leave_owner_codex_runtime(agent) is False
    assert agent._current_main_runtime()["api_mode"] == "codex_app_server"


def test_owner_session_approval_fails_closed_without_a_bypass(agent, monkeypatch):
    from tools import approval

    monkeypatch.setattr(approval, "is_approval_bypass_active", lambda: False)
    assert owner_session_approval(agent)[1] is False

    monkeypatch.setattr(approval, "is_approval_bypass_active", lambda: True)
    assert owner_session_approval(agent)[1] is True
