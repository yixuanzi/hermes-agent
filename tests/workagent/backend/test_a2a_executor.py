from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from tools.environments.base import _current_user_env_for_shell
from tools.user_env_runtime import (
    bind_current_user_env_identity_from_agent,
    get_current_user_env_identity,
)
import tools.user_env_runtime as user_env_runtime


# A list of one: the contract below should hold for any A2A executor, but
# WORKAGENT ships the only one. `aisoc.backend.a2a_service` does not exist in
# this fork -- aisoc/backend/ has no a2a_service package and no
# HermesA2AExecutor -- so parametrizing over it only ever raised ImportError.
@pytest.mark.parametrize(
    "module_name",
    ["workagent.backend.a2a_service.executor"],
)
def test_a2a_platform_suffix_preserves_origin_user_env(
    module_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor_module = importlib.import_module(module_name)
    executor = executor_module.HermesA2AExecutor()

    monkeypatch.setattr(
        user_env_runtime,
        "load_user_env",
        lambda platform, user_id, user_name: SimpleNamespace(
            env={"ORIGIN_PLATFORM": platform},
        ),
    )

    class _Agent:
        platform = "workagent-a2a"
        _pending_source_meta = {
            "platform": "slack",
            "uid": "user-123",
            "uname": "Alice",
        }

        def run_conversation(self, *args, **kwargs):
            del args, kwargs
            with bind_current_user_env_identity_from_agent(self):
                identity = get_current_user_env_identity()
                self.observed = {
                    "platform": self.platform,
                    "user_env_platform": self._user_env_platform,
                    "identity_platform": identity.platform if identity else None,
                    "shell_env": _current_user_env_for_shell(),
                }
            return {"final_response": "ok"}

    agent = _Agent()

    result = executor._run_agent_conversation(
        agent,
        "hello",
        [],
        "task-123",
        None,
    )

    assert result == {"final_response": "ok"}
    assert agent.observed == {
        "platform": "slack_a2a",
        "user_env_platform": "slack",
        "identity_platform": "slack",
        "shell_env": {"ORIGIN_PLATFORM": "slack"},
    }
    assert agent.platform == "workagent-a2a"
    assert not hasattr(agent, "_user_env_platform")
