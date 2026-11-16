"""ext-tools /userenv：按调用者身份隔离的个人环境变量。"""

import importlib.util
import py_compile
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

import userenv_cmd
from tools.user_env_runtime import (
    reset_current_user_env_identity,
    set_current_user_env_identity,
)


@pytest.fixture
def as_user():
    tokens = []

    def _as(user_id, name):
        tokens.append(set_current_user_env_identity("feishu", user_id, name))

    yield _as
    for token in reversed(tokens):
        reset_current_user_env_identity(token)


class FakeCtx:
    def __init__(self):
        self.cmds = {}
        self.tools = []
        self.hooks = []

    def register_command(self, name, handler, description="", args_hint=""):
        self.cmds[name] = (handler, description, args_hint)

    def register_tool(self, name, toolset="", schema=None, handler=None, **kw):
        self.tools.append(name)

    def register_hook(self, name, callback):
        self.hooks.append(name)


def test_no_identity_fails_closed():
    assert "无法识别" in userenv_cmd.userenv_command("list")


def test_set_list_get_masks_value(as_user):
    as_user("userA", "Alice")
    assert "暂无" in userenv_cmd.userenv_command("list")
    assert "已设置" in userenv_cmd.userenv_command("set MY_KEY sk-abcdef1234567890xyz")
    assert "MY_KEY" in userenv_cmd.userenv_command("list")
    shown = userenv_cmd.userenv_command("get MY_KEY")
    assert "sk-a" in shown and "0xyz" in shown
    assert "abcdef" not in shown and "1234567" not in shown


def test_spaced_and_short_values(as_user):
    as_user("userA", "Alice")
    userenv_cmd.userenv_command("set SPACED hello world 123")
    spaced = userenv_cmd.userenv_command("get SPACED")
    assert "hell" in spaced and "123" in spaced
    userenv_cmd.userenv_command("set SHORT abc")
    short = userenv_cmd.userenv_command("get SHORT")
    assert "*" in short and "abc" not in short.replace("*", "")


def test_users_are_isolated(as_user):
    as_user("userA", "Alice")
    userenv_cmd.userenv_command("set MY_KEY sk-abcdef1234567890xyz")
    as_user("userB", "Bob")
    assert "MY_KEY" not in userenv_cmd.userenv_command("list")
    assert "不存在" in userenv_cmd.userenv_command("get MY_KEY")


def test_delete(as_user):
    as_user("userA", "Alice")
    userenv_cmd.userenv_command("set MY_KEY sk-abcdef1234567890xyz")
    assert "已删除" in userenv_cmd.userenv_command("delete MY_KEY")
    assert "不存在" in userenv_cmd.userenv_command("get MY_KEY")
    assert "不存在" in userenv_cmd.userenv_command("delete NOPE")


def test_help_and_unknown_subcommand(as_user):
    helptext = userenv_cmd.userenv_command("")
    assert "/userenv" in helptext and "list" in helptext
    as_user("userA", "Alice")
    assert "未知子命令" in userenv_cmd.userenv_command("frobnicate x")


def test_register_commands():
    ctx = FakeCtx()
    userenv_cmd.register(ctx)
    assert "userenv" in ctx.cmds

    package = sys.modules["hermes_plugins.ext_tools"]  # loaded as the plugin loader does (conftest)
    pkg_ctx = FakeCtx()
    package.register(pkg_ctx)
    assert "userenv" in pkg_ctx.cmds


def test_gateway_run_compiles(tmp_path):
    py_compile.compile(str(REPO / "gateway" / "run.py"), cfile=str(tmp_path / "run.pyc"), doraise=True)
