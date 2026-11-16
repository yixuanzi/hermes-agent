import json
from pathlib import Path


def _minimal_local_terminal_config(cwd: str) -> dict:
    return {
        "env_type": "local",
        "cwd": cwd,
        "timeout": 15,
    }


def test_terminal_tool_isolates_local_environment_cache_by_platform_and_user_id(monkeypatch, tmp_path):
    from tools import terminal_tool
    from tools.user_env_runtime import reset_current_user_env_identity, set_current_user_env_identity

    created_task_ids = []

    class FakeEnv:
        env = {}

        def execute(self, command, **kwargs):
            return {"output": command, "returncode": 0}

    def fake_create_environment(*args, **kwargs):
        created_task_ids.append(kwargs["task_id"])
        return FakeEnv()

    monkeypatch.setattr(terminal_tool, "_active_environments", {})
    monkeypatch.setattr(terminal_tool, "_last_activity", {})
    monkeypatch.setattr(terminal_tool, "_creation_locks", {})
    monkeypatch.setattr(terminal_tool, "_task_env_overrides", {})
    monkeypatch.setattr(terminal_tool, "_get_env_config", lambda: _minimal_local_terminal_config(str(tmp_path)))
    monkeypatch.setattr("tools.terminal_tool_backends._create_environment", fake_create_environment)
    monkeypatch.setattr(terminal_tool, "_start_cleanup_thread", lambda: None)
    monkeypatch.setattr(terminal_tool, "_check_all_guards", lambda command, env_type, **kwargs: {"approved": True})

    alice = set_current_user_env_identity("slack", "u123", "alice")
    try:
        assert json.loads(terminal_tool.terminal_tool("pwd"))["exit_code"] == 0
    finally:
        reset_current_user_env_identity(alice)

    bob = set_current_user_env_identity("slack", "u456", "bob")
    try:
        assert json.loads(terminal_tool.terminal_tool("pwd"))["exit_code"] == 0
    finally:
        reset_current_user_env_identity(bob)

    alice_renamed = set_current_user_env_identity("slack", "u123", "alice-renamed")
    try:
        assert json.loads(terminal_tool.terminal_tool("pwd"))["exit_code"] == 0
    finally:
        reset_current_user_env_identity(alice_renamed)

    assert created_task_ids == ["local::slack::u123", "local::slack::u456"]


def test_local_environment_userenv_overlay_does_not_persist_in_snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from tools.environments.local import LocalEnvironment
    from tools.user_env_runtime import reset_current_user_env_identity, set_current_user_env_identity
    from tools.user_env_store import delete_user_env_var, set_user_env_var

    token = set_current_user_env_identity("slack", "u123", "alice")
    env = None
    try:
        set_user_env_var("slack", "u123", "alice", "FOO", "1")
        env = LocalEnvironment(cwd=str(tmp_path), timeout=15)

        first = env.execute("python3 -c 'import os; print(os.environ.get(\"FOO\", \"\"))'")
        first_snapshot = Path(env._snapshot_path).read_text(encoding="utf-8")

        set_user_env_var("slack", "u123", "alice", "FOO", "2")
        second = env.execute("python3 -c 'import os; print(os.environ.get(\"FOO\", \"\"))'")

        delete_user_env_var("slack", "u123", "alice", "FOO")
        third = env.execute("python3 -c 'import os; print(os.environ.get(\"FOO\", \"\"))'")
        final_snapshot = Path(env._snapshot_path).read_text(encoding="utf-8")
    finally:
        if env is not None:
            env.cleanup()
        reset_current_user_env_identity(token)

    assert first["output"].strip() == "1"
    assert second["output"].strip() == "2"
    assert third["output"].strip() == ""
    assert "declare -x FOO=" not in first_snapshot
    assert "declare -x FOO=" not in final_snapshot
