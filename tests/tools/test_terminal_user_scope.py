"""On the local backend the shell follows the bound platform user, never the session (userenv)."""

from contextvars import copy_context

from tools.terminal_tool import _env_cache_key, _resolve_container_task_id
from tools.user_env_runtime import set_current_user_env_identity


def _key(task_id, env_type, user=None, name=""):
    def run():
        if user:
            set_current_user_env_identity("feishu", user, name)
        return _env_cache_key(task_id, env_type)
    return copy_context().run(run)


def test_one_user_keeps_one_local_shell_across_sessions_and_renames():
    assert _key("s1", "local", "u1", "Alice") == _key("s2", "local", "u1", "Bob")


def test_two_users_never_share_a_local_shell():
    assert _key("s1", "local", "u1") != _key("s1", "local", "u2")


def test_unbound_calls_and_container_backends_keep_the_task_key():
    assert _key("s1", "local") == _resolve_container_task_id("s1")
    assert _key("s1", "docker", "u1") == _key("s1", "docker", "u2") == _resolve_container_task_id("s1")
