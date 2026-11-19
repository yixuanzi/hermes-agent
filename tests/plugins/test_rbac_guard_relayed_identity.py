"""rbac-guard resolves a relayed turn's role from the originating platform.

The workagent A2A executor marks a relayed turn's platform as
``<origin>_a2a`` (``feishu_a2a``) while the user's identity — and so the role
row in ``user_roles`` — belongs to ``<origin>``.  The role must be the one
recorded for the originating platform, for the prompt block and for the tool
gate alike.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


PLUGIN_INIT = Path(__file__).resolve().parents[2] / "plugins" / "rbac-guard" / "__init__.py"


@pytest.fixture
def rbac_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    # role_rules.json is deployment config and gitignored; load the shipped example instead.
    monkeypatch.setenv(
        "AEGIS_RBAC_RULES_PATH", str(PLUGIN_INIT.with_name("role_rules.json.example"))
    )
    module_name = f"rbac_guard_relay_test_{id(tmp_path)}"
    spec = importlib.util.spec_from_file_location(
        module_name, PLUGIN_INIT, submodule_search_locations=[str(PLUGIN_INIT.parent)]
    )
    assert spec is not None and spec.loader is not None
    plugin = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = plugin
    try:
        spec.loader.exec_module(plugin)
        yield plugin
    finally:
        plugin.roles.close_role_db()
        sys.modules.pop(module_name, None)


def _admin_only_tool(roles) -> str:
    """A tool the shipped rules give admin but deny to user."""
    admin, user = roles.ROLE_RULES["admin"], roles.ROLE_RULES["user"]
    for tool in sorted(set(user.get("denied_tools") or [])):
        if roles.tool_allowed(admin, tool) and not roles.tool_allowed(user, tool):
            return tool
    pytest.skip("shipped role rules have no admin-only tool")


def test_a_relayed_turn_gets_the_originating_platforms_role(rbac_plugin) -> None:
    roles = rbac_plugin.roles
    assert roles.set_role("feishu", "u-admin", "admin") is True
    tool = _admin_only_tool(roles)

    injected = rbac_plugin.on_pre_llm_call(
        session_id="mk-1-abc",
        platform="feishu_a2a",
        sender_id="u-admin",
        turn_id="turn-1",
        task_id="task-1",
    )["context"]
    gate = rbac_plugin.on_pre_tool_call(
        tool_name=tool, args={}, session_id="mk-1-abc", turn_id="turn-1", task_id="task-1"
    )

    assert "当前用户身份: feishu:u-admin;" in injected
    assert "当前用户角色: admin" in injected
    assert gate is None or gate.get("action") != "block"


def test_an_unrelayed_platform_is_looked_up_as_given(rbac_plugin) -> None:
    roles = rbac_plugin.roles
    assert roles.set_role("feishu", "u-admin", "admin") is True

    injected = rbac_plugin.on_pre_llm_call(
        session_id="s", platform="a2a", sender_id="u-admin", turn_id="t", task_id="k"
    )["context"]

    # A bare transport name carries no originating platform to fall back to.
    assert "当前用户身份: a2a:u-admin;" in injected
    assert "当前用户角色: user" in injected
