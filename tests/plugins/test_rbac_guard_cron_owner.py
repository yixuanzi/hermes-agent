"""rbac-guard resolves an owned cron job's role from its owner's platform.

A cron agent's platform is always ``cron`` (``cron.jobs_identify.as_job_owner``) while its
``sender_id`` is the owner's platform user id from the job's ``identify``, so a lookup keyed
``cron:<uid>`` found no role row and ran the owner's job as ``user``. The owner's platform is the
userenv identity ``run_job`` binds for the whole fire.
"""

from __future__ import annotations

import contextvars
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from cron.jobs_identify import as_job_owner, runs_as_job_owner
from tools.user_env_runtime import bind_current_user_env_identity


PLUGIN_INIT = Path(__file__).resolve().parents[2] / "plugins" / "rbac-guard" / "__init__.py"
OWNER = {"platform": "feishu", "user_id": "u-admin", "user_name": "Admin"}


@pytest.fixture
def rbac_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("AEGIS_RBAC_RULES_PATH", raising=False)
    module_name = f"rbac_guard_cron_test_{id(tmp_path)}"
    spec = importlib.util.spec_from_file_location(
        module_name, PLUGIN_INIT, submodule_search_locations=[str(PLUGIN_INIT.parent)]
    )
    assert spec is not None and spec.loader is not None
    plugin = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = plugin
    try:
        spec.loader.exec_module(plugin)
        assert plugin.roles.set_role("feishu", "u-admin", "admin") is True
        yield plugin
    finally:
        plugin.roles.close_role_db()
        sys.modules.pop(module_name, None)


def _admin_only_tool(roles) -> str:
    admin, user = roles.ROLE_RULES["admin"], roles.ROLE_RULES["user"]
    for tool in sorted(set(user.get("denied_tools") or [])):
        if roles.tool_allowed(admin, tool) and not roles.tool_allowed(user, tool):
            return tool
    pytest.skip("shipped role rules have no admin-only tool")


def _cron_turn(plugin, sender_id: str) -> str:
    return plugin.on_pre_llm_call(
        session_id="local-run", platform="cron", sender_id=sender_id,
        turn_id="turn-1", task_id="cron:job-1:exec-1",
    )["context"]


def test_an_owned_job_turn_gets_the_owners_role_through_the_scheduler_seams(rbac_plugin) -> None:
    """The fire as run_job drives it: owner bound by the decorator, agent carried by as_job_owner,
    the turn on a copied context (the scheduler's agent worker thread)."""
    job = {"id": "job-1", "identify": dict(OWNER)}
    tool = _admin_only_tool(rbac_plugin.roles)

    @runs_as_job_owner
    def fire(job):
        agent = as_job_owner(job, SimpleNamespace(platform="cron"))

        def turn():  # agent/turn_context.py's pre_llm_call kwargs
            injected = rbac_plugin.on_pre_llm_call(
                session_id="local-run", platform=agent.platform, sender_id=agent._user_id,
                turn_id="turn-1", task_id="cron:job-1:exec-1",
            )["context"]
            gate = rbac_plugin.on_pre_tool_call(
                tool_name=tool, args={}, session_id="local-run", turn_id="turn-1",
                task_id="cron:job-1:exec-1",
            )
            return injected, gate

        return contextvars.copy_context().run(turn)

    injected, gate = fire(job)

    assert "当前用户身份: feishu:u-admin;" in injected
    assert "当前用户角色: admin" in injected
    assert gate is None or gate.get("action") != "block"


def test_a_public_job_turn_stays_a_cron_identity(rbac_plugin) -> None:
    injected = _cron_turn(rbac_plugin, sender_id="")

    assert "当前用户身份: cron:local;" in injected
    assert "当前用户角色: user" in injected


def test_a_bound_identity_for_another_user_is_not_borrowed(rbac_plugin) -> None:
    with bind_current_user_env_identity("feishu", "u-admin", "Admin"):
        injected = _cron_turn(rbac_plugin, sender_id="u-other")

    assert "当前用户身份: cron:u-other;" in injected
    assert "当前用户角色: user" in injected


def test_only_cron_turns_are_remapped(rbac_plugin) -> None:
    with bind_current_user_env_identity("feishu", "u-admin", "Admin"):
        injected = rbac_plugin.on_pre_llm_call(
            session_id="s", platform="slack", sender_id="u-admin", turn_id="t", task_id="k",
        )["context"]

    assert "当前用户身份: slack:u-admin;" in injected
    assert "当前用户角色: user" in injected
