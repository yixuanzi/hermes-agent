import json


def _write_users_env(tmp_path, payload):
    path = tmp_path / "users.env.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def test_make_run_env_injects_current_platform_user_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_users_env(
        tmp_path,
        {"slack.u123": {"CURRENT_USER_NAME": "alice", "CUSTOM_TOKEN": "abc123"}},
    )

    from tools.environments.local import _make_run_env
    from tools.user_env_runtime import reset_current_user_env_identity, set_current_user_env_identity

    token = set_current_user_env_identity("slack", "u123", "alice")
    try:
        env = _make_run_env({"PATH": "/usr/bin:/bin"})
    finally:
        reset_current_user_env_identity(token)

    assert env["CUSTOM_TOKEN"] == "abc123"
    assert env["CURRENT_USER_NAME"] == "alice"


def test_make_run_env_does_not_leak_between_user_identities(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_users_env(
        tmp_path,
        {
            "slack.u123": {"CURRENT_USER_NAME": "alice", "CUSTOM_TOKEN": "alice-token"},
            "slack.u456": {"CURRENT_USER_NAME": "bob", "CUSTOM_TOKEN": "bob-token"},
        },
    )

    from tools.environments.local import _make_run_env
    from tools.user_env_runtime import reset_current_user_env_identity, set_current_user_env_identity

    alice_token = set_current_user_env_identity("slack", "u123", "alice")
    try:
        alice_env = _make_run_env({"PATH": "/usr/bin:/bin"})
    finally:
        reset_current_user_env_identity(alice_token)

    bob_token = set_current_user_env_identity("slack", "u456", "bob")
    try:
        bob_env = _make_run_env({"PATH": "/usr/bin:/bin"})
    finally:
        reset_current_user_env_identity(bob_token)

    assert alice_env["CUSTOM_TOKEN"] == "alice-token"
    assert bob_env["CUSTOM_TOKEN"] == "bob-token"
