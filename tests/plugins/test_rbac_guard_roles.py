from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import ModuleType

import pytest


PLUGIN_ROLES = Path(__file__).resolve().parents[2] / "plugins" / "rbac-guard" / "roles.py"
PLUGIN_INIT = PLUGIN_ROLES.with_name("__init__.py")


@pytest.fixture
def rbac_roles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    module_name = f"rbac_guard_roles_test_{id(tmp_path)}"
    spec = importlib.util.spec_from_file_location(module_name, PLUGIN_ROLES)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    module.close_role_db()


def test_role_lookup_reads_aegis_user_roles_dynamically(rbac_roles: ModuleType) -> None:
    assert rbac_roles.role_for("feishu", "u-1") == "user"

    assert rbac_roles.set_role("feishu", "u-1", "admin") is True
    assert rbac_roles.role_for("feishu", "u-1") == "admin"

    with sqlite3.connect(rbac_roles._ROLE_DB_PATH) as connection:
        connection.execute(
            "UPDATE user_roles SET role = 'user' WHERE platform = 'feishu' AND uid = 'u-1'"
        )
        connection.commit()

    assert rbac_roles.role_for("feishu", "u-1") == "user"
    assert rbac_roles.get_role("feishu", "u-1")["summary"] == "普通用户"


def test_role_rules_are_loaded_from_json_without_rank(
    rbac_roles: ModuleType,
) -> None:
    assert set(rbac_roles.ROLE_RULES) == {"admin", "operator", "user"}
    for rule in rbac_roles.ROLE_RULES.values():
        assert "rank" not in rule
        assert set(rule) == {
            "summary",
            "prompt_constraints",
            "allow_tools",
            "denied_tools",
            "tools_paras",
        }

    assert rbac_roles.DANGEROUS_PATTERN.pattern == (
        r"(rm\s+-rf|git\s+push|drop\s+(table|database)|shutdown|reboot|mkfs|:\(\)\{)"
    )
    assert rbac_roles.set_role("feishu", "u-3", "user") is True
    prompt = rbac_roles.prompt_block("feishu", "u-3")
    assert "rank" not in prompt
    assert "tools_paras" not in prompt
    assert "工具参数约束" not in prompt


def test_dangerous_pattern_is_loaded_from_the_role_rules_root(
    rbac_roles: ModuleType,
    tmp_path: Path,
) -> None:
    payload = json.loads(rbac_roles._ROLE_RULES_PATH.read_text(encoding="utf-8"))
    payload["dangerous_pattern"] = r"erase_everything"
    path = tmp_path / "custom-role-rules.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    rules, dangerous_pattern = rbac_roles._load_role_rules_config(path)

    assert set(rules) == {"admin", "operator", "user"}
    assert dangerous_pattern.search("please erase_everything now")
    assert not dangerous_pattern.search("rm -rf /tmp")

    payload["dangerous_pattern"] = "["
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(rbac_roles.RoleRulesConfigError, match="dangerous_pattern"):
        rbac_roles._load_role_rules_config(path)


def test_invalid_role_rules_config_fails_validation(
    rbac_roles: ModuleType,
    tmp_path: Path,
) -> None:
    valid = json.loads(rbac_roles._ROLE_RULES_PATH.read_text(encoding="utf-8"))

    missing_role = dict(valid)
    missing_role.pop("admin")
    missing_path = tmp_path / "missing-role.json"
    missing_path.write_text(json.dumps(missing_role), encoding="utf-8")
    with pytest.raises(rbac_roles.RoleRulesConfigError):
        rbac_roles._load_role_rules(missing_path)

    invalid_regex = json.loads(json.dumps(valid))
    invalid_regex["user"]["tools_paras"] = {"terminal": [{"command": "["}]}
    regex_path = tmp_path / "invalid-regex.json"
    regex_path.write_text(json.dumps(invalid_regex), encoding="utf-8")
    with pytest.raises(rbac_roles.RoleRulesConfigError):
        rbac_roles._load_role_rules(regex_path)

    legacy_rank = json.loads(json.dumps(valid))
    legacy_rank["admin"]["rank"] = 100
    rank_path = tmp_path / "legacy-rank.json"
    rank_path.write_text(json.dumps(legacy_rank), encoding="utf-8")
    with pytest.raises(rbac_roles.RoleRulesConfigError):
        rbac_roles._load_role_rules(rank_path)


def test_legacy_unknown_role_is_removed_from_plugin_config(
    rbac_roles: ModuleType,
    tmp_path: Path,
) -> None:
    valid = json.loads(rbac_roles._ROLE_RULES_PATH.read_text(encoding="utf-8"))
    valid["unknown"] = {
        "summary": "Legacy fallback",
        "prompt_constraints": [],
        "allow_tools": ["rbac_status"],
        "denied_tools": [],
        "tools_paras": {},
    }
    path = tmp_path / "legacy-role-rules.json"
    path.write_text(json.dumps(valid), encoding="utf-8")

    loaded = rbac_roles._load_role_rules(path)

    assert set(loaded) == {"admin", "operator", "user"}
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert set(persisted) == {
        "admin",
        "operator",
        "user",
        "dangerous_pattern",
    }


def test_tools_paras_matches_only_parameters_present_in_the_call(
    rbac_roles: ModuleType,
) -> None:
    role = rbac_roles.get_role("cli", "local")
    role["allow_tools"] = None
    role["denied_tools"] = []
    role["tools_paras"] = {
        "terminal": [{
            "command": r"^ls",
            "cwd": r"/workspace",
        }]
    }

    assert rbac_roles.tool_allowed(role, "terminal") is True
    assert rbac_roles.tool_params_allowed(
        role,
        "terminal",
        {"command": "ls -la", "cwd": "/workspace/project"},
    ) == (True, "")
    assert rbac_roles.tool_params_allowed(
        role,
        "terminal",
        {"command": "rm -rf /", "cwd": "/workspace/project"},
    )[0] is False
    # "cwd" is configured but absent from the call: it is skipped rather
    # than treated as a failure, so the still-present "command" decides.
    assert rbac_roles.tool_params_allowed(
        role,
        "terminal",
        {"command": "ls -la"},
    ) == (True, "")
    assert rbac_roles.tool_params_allowed(
        role,
        "terminal",
        {"command": "rm -rf /"},
    )[0] is False
    assert rbac_roles.tool_params_allowed(role, "read_file", {}) == (True, "")


def test_tools_paras_also_constrains_admin_and_denied_tools_win(
    rbac_roles: ModuleType,
) -> None:
    admin = rbac_roles.ROLE_RULES["admin"]
    admin["tools_paras"] = {"terminal": [{"command": r"^ls"}]}
    assert rbac_roles.tool_allowed(admin, "terminal") is True
    assert rbac_roles.tool_params_allowed(admin, "terminal", {"command": "rm"})[0] is False

    admin["denied_tools"] = ["terminal"]
    assert rbac_roles.tool_allowed(admin, "terminal") is False

    allow_and_deny = {"allow_tools": ["terminal"], "denied_tools": ["terminal"]}
    assert rbac_roles.tool_allowed(allow_and_deny, "terminal") is False


def test_plugin_hook_enforces_parameter_rules_and_status_has_no_rank(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("HERMES_RBAC_AUDIT", str(tmp_path / "audit.log"))

    module_name = f"rbac_guard_plugin_test_{id(tmp_path)}"
    spec = importlib.util.spec_from_file_location(
        module_name,
        PLUGIN_INIT,
        submodule_search_locations=[str(PLUGIN_INIT.parent)],
    )
    assert spec is not None and spec.loader is not None
    plugin = importlib.util.module_from_spec(spec)
    plugin.__package__ = module_name
    plugin.__path__ = [str(PLUGIN_INIT.parent)]
    sys.modules[module_name] = plugin
    try:
        spec.loader.exec_module(plugin)
        plugin.roles.ROLE_RULES["user"]["tools_paras"] = {
            "read_file": [{"path": r"^/safe"}]
        }
        assert plugin.roles.set_role("cli", "u-4", "user") is True
        plugin.on_pre_llm_call(
            session_id="session-4",
            platform="cli",
            sender_id="u-4",
            turn_id="turn-4",
        )

        # A shared group session must keep identities isolated per turn.
        plugin.on_pre_llm_call(
            session_id="shared-session",
            platform="feishu",
            sender_id="creator",
            turn_id="turn-a",
        )
        plugin.on_pre_llm_call(
            session_id="shared-session",
            platform="feishu",
            sender_id="participant",
            turn_id="turn-b",
        )
        assert plugin._identity_cache_key("shared-session", "turn-a") == (
            "shared-session:turn-a"
        )
        assert plugin.resolve_identity("shared-session", "turn-a") == (
            "feishu",
            "creator",
        )
        assert plugin.resolve_identity("shared-session", "turn-b") == (
            "feishu",
            "participant",
        )
        # A legacy call without turn_id must not fall back to either user's
        # session-level identity.
        assert plugin.resolve_identity("shared-session") == ("cli", "local")

        assert plugin.on_pre_tool_call(
            tool_name="read_file",
            args={"path": "/safe/report.txt"},
            session_id="session-4",
            turn_id="turn-4",
        ) is None
        blocked = plugin.on_pre_tool_call(
            tool_name="read_file",
            args={"path": "/etc/passwd"},
            session_id="session-4",
            turn_id="turn-4",
        )
        assert blocked is not None
        assert blocked["action"] == "block"

        status = json.loads(plugin._tool_rbac_status({"platform": "cli", "user_id": "u-4"}))
        assert "rank" not in status
        assert status["tools_paras"] == {"read_file": [{"path": r"^/safe"}]}
        assert status["dangerous_pattern"] == plugin.roles.DANGEROUS_PATTERN.pattern
    finally:
        plugin.roles.close_role_db()
        sys.modules.pop(module_name, None)
        sys.modules.pop("roles", None)


def test_role_storage_uses_one_reusable_sqlite_connection(
    rbac_roles: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_connect = rbac_roles.sqlite3.connect
    connections: list[sqlite3.Connection] = []

    def tracked_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(rbac_roles.sqlite3, "connect", tracked_connect)

    assert rbac_roles.role_for("telegram", "u-2") == "user"
    assert rbac_roles.role_for("telegram", "u-2") == "user"
    assert rbac_roles.set_role("telegram", "u-2", "operator") is True
    assert rbac_roles.role_for("telegram", "u-2") == "operator"
    assert len(connections) == 1


def test_multiple_tool_parameter_rules_use_and_logic(rbac_roles: ModuleType) -> None:
    role = rbac_roles.get_role("cli", "local")
    role["tools_paras"] = {
        "write_file": [
            {"path": r"^/output/"},
            {"path": r"(?<!\.secret)$"},
        ]
    }

    assert rbac_roles.tool_params_allowed(
        role, "write_file", {"path": "/output/report.txt"}
    ) == (True, "")
    allowed, reason = rbac_roles.tool_params_allowed(
        role, "write_file", {"path": "/output/report.secret"}
    )
    assert allowed is False
    assert "rule 2" in reason


def test_legacy_single_tool_parameter_map_is_migrated_by_plugin(
    rbac_roles: ModuleType,
    tmp_path: Path,
) -> None:
    payload = json.loads(rbac_roles._ROLE_RULES_PATH.read_text(encoding="utf-8"))
    payload["user"]["tools_paras"] = {"write_file": {"path": r"^/output/"}}
    path = tmp_path / "legacy-tool-rules.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = rbac_roles._load_role_rules(path)

    assert loaded["user"]["tools_paras"] == {"write_file": [{"path": r"^/output/"}]}
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["user"]["tools_paras"] == {"write_file": [{"path": r"^/output/"}]}


def test_rbac_audit_is_disabled_by_default_and_requires_true(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # _AUDIT_ENABLED is a module-level constant read once at import time (like
    # role_rules.json, it needs a restart to pick up a new value), so each
    # scenario needs its own fresh import with the env var set beforehand —
    # setting it on an already-imported module has no effect.
    def load_with_audit_env(value: str | None) -> ModuleType:
        hermes_home = tmp_path / f"hermes-{value}"
        hermes_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        if value is None:
            monkeypatch.delenv("AEGIS_RBAC_AUDIT", raising=False)
        else:
            monkeypatch.setenv("AEGIS_RBAC_AUDIT", value)
        module_name = f"rbac_guard_roles_audit_test_{id(tmp_path)}_{len(str(value))}_{value}"
        spec = importlib.util.spec_from_file_location(module_name, PLUGIN_ROLES)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    module = load_with_audit_env(None)
    audit_path = tmp_path / "audit-default.log"
    monkeypatch.setattr(module, "_AUDIT_PATH", audit_path)
    module.audit("disabled_by_default")
    assert not audit_path.exists()
    module.close_role_db()

    module = load_with_audit_env("1")
    audit_path = tmp_path / "audit-one.log"
    monkeypatch.setattr(module, "_AUDIT_PATH", audit_path)
    module.audit("not_exactly_true")
    assert not audit_path.exists()
    module.close_role_db()

    module = load_with_audit_env(" true ")
    audit_path = tmp_path / "audit-true.log"
    monkeypatch.setattr(module, "_AUDIT_PATH", audit_path)
    module.audit("enabled", identity="cli:local")
    assert audit_path.exists()
    assert '"event": "enabled"' in audit_path.read_text(encoding="utf-8")
    module.close_role_db()


def test_set_role_only_accepts_roles_backed_by_the_database_table(
    rbac_roles: ModuleType,
) -> None:
    assert rbac_roles.PERSISTED_ROLES == ("user", "operator", "admin")
    assert rbac_roles.set_role("cli", "local", "viewer") is False
    assert rbac_roles.role_for("cli", "local") == "user"

    assert rbac_roles.set_role("cli", "local", "user") is True
    with sqlite3.connect(rbac_roles._ROLE_DB_PATH) as connection:
        row = connection.execute(
            "SELECT platform, uid, uname, role FROM user_roles "
            "WHERE platform = 'cli' AND uid = 'local'"
        ).fetchone()
    assert row == ("cli", "local", "local", "user")
