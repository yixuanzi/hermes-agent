"""Tool execution acts for the agent's platform user (``userenv``)."""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from run_agent import AIAgent


def _tool_defs(*names: str) -> list:
    return [{"type": "function", "function": {"name": n, "description": n, "parameters": {"type": "object", "properties": {}}}}
            for n in names]


@pytest.fixture(autouse=True)
def _no_plugin_discovery(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", lambda: None)


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "users.env.json").write_text(
        json.dumps({"slack.u123": {"CURRENT_USER_NAME": "alice", "CUSTOM_TOKEN": "abc123"}}), encoding="utf-8")
    for key in ("HERMES_SESSION_PLATFORM", "HERMES_SESSION_USER_ID", "HERMES_SESSION_USER_NAME"):
        monkeypatch.delenv(key, raising=False)
    with patch("model_tools.get_tool_definitions", return_value=_tool_defs("web_search", "read_file")), \
         patch("model_tools.check_toolset_requirements", return_value={}), \
         patch("agent.process_bootstrap.OpenAI"):
        a = AIAgent(api_key="test-key-1234567890", base_url="https://openrouter.ai/api/v1",
                    quiet_mode=True, skip_context_files=True, skip_memory=True)
    a.client = MagicMock()
    # A cron-style agent: no gateway session env, the user lives on the agent itself.
    a._user_env_platform, a._user_id, a._user_name = "slack", "u123", "alice"
    return a


def _call(name: str, arguments: str = "{}"):
    return SimpleNamespace(id=f"call_{uuid.uuid4().hex[:8]}", type="function",
                           function=SimpleNamespace(name=name, arguments=arguments))


_ALICE = '{"identity": "slack.u123", "token": "abc123"}'


def _report_identity(*_args, **_kwargs) -> str:
    from tools.user_env_runtime import get_current_user_env_identity, get_current_user_env_values
    identity = get_current_user_env_identity()
    return json.dumps({"identity": identity.user_key if identity else None,
                       "token": get_current_user_env_values().get("CUSTOM_TOKEN", "missing")})


@pytest.mark.parametrize("quiet_mode", [True, False])
def test_a_sequential_registry_tool_sees_the_agents_user_without_session_env(agent, quiet_mode):
    agent.quiet_mode = quiet_mode
    messages: list = []

    with patch("model_tools.handle_function_call", side_effect=_report_identity):
        agent._execute_tool_calls_sequential(SimpleNamespace(content="", tool_calls=[_call("web_search")]),
                                             messages, "task-1")

    assert _ALICE in messages[0]["content"]  # web results arrive inside an untrusted-content envelope


def test_concurrent_registry_tools_each_see_the_agents_user(agent):
    messages: list = []
    calls = [_call("web_search", '{"q":"a"}'), _call("read_file", '{"path":"/tmp/x"}')]

    with patch("model_tools.handle_function_call", side_effect=_report_identity):
        agent._execute_tool_calls_concurrent(SimpleNamespace(content="", tool_calls=calls), messages, "task-1")

    results = [m["content"] for m in messages if m.get("role") == "tool"]
    assert len(results) == 2 and all(_ALICE in r for r in results)


def test_the_binding_does_not_outlive_the_call(agent):
    from tools.user_env_runtime import get_current_user_env_identity

    with patch("model_tools.handle_function_call", side_effect=_report_identity):
        agent._execute_tool_calls_sequential(SimpleNamespace(content="", tool_calls=[_call("web_search")]), [], "t")

    assert get_current_user_env_identity() is None
