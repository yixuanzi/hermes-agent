from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


RULE_FIELDS = {
    "summary": "A role summary",
    "prompt_constraints": ["Read only"],
    "allow_tools": None,
    "denied_tools": [],
    "tools_paras": {"terminal": [{"command": "^ls(\\s|$)"}]},
}
DANGEROUS_PATTERN = r"(rm\s+-rf|git\s+push|drop\s+(table|database)|shutdown|reboot|mkfs|:\(\)\{)"


def _rules_payload() -> dict[str, object]:
    rules = {
        role: {
            **RULE_FIELDS,
            "summary": f"{role} summary",
            "prompt_constraints": [f"{role} constraint"],
            "tools_paras": {},
        }
        for role in ("admin", "operator", "user")
    }
    return {"dangerous_pattern": DANGEROUS_PATTERN, **rules}


@pytest.fixture(autouse=True)
def configured_rules_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "rbac" / "role_rules.json"
    path.parent.mkdir()
    path.write_text(json.dumps(_rules_payload()), encoding="utf-8")
    monkeypatch.setenv("AEGIS_RBAC_RULES_PATH", str(path))
    return path


def test_rules_path_defaults_to_hermes_root_and_supports_relative_and_absolute_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aegis.backend.services.rbac_rule_service import (
        DEFAULT_RBAC_RULES_PATH,
        resolve_rbac_rules_path,
    )

    monkeypatch.delenv("AEGIS_RBAC_RULES_PATH", raising=False)
    expected_default = Path(__file__).resolve().parents[3] / DEFAULT_RBAC_RULES_PATH
    assert resolve_rbac_rules_path() == expected_default

    monkeypatch.setenv("AEGIS_RBAC_RULES_PATH", "custom/rules.json")
    assert resolve_rbac_rules_path() == expected_default.parents[2] / "custom/rules.json"

    absolute = tmp_path / "absolute-rules.json"
    monkeypatch.setenv("AEGIS_RBAC_RULES_PATH", str(absolute))
    assert resolve_rbac_rules_path() == absolute


def test_admin_can_read_and_update_one_role_without_overwriting_the_others(
    client: TestClient,
    auth_headers: dict[str, str],
    configured_rules_file: Path,
) -> None:
    listed = client.get("/api/rbac-rules", headers=auth_headers)
    assert listed.status_code == 200
    assert set(listed.json()["rules"]) == {"admin", "operator", "user"}

    updated_rule = {
        "summary": "Updated user summary",
        "prompt_constraints": ["line one\nline two"],
        "allow_tools": ["read_file"],
        "denied_tools": ["terminal"],
        "tools_paras": {"read_file": [{"path": "^/safe/"}]},
    }
    updated = client.put("/api/rbac-rules/user", headers=auth_headers, json=updated_rule)
    assert updated.status_code == 200
    assert updated.json() == {
        "role": "user",
        "rule": updated_rule,
        "restart_required": True,
    }

    persisted = json.loads(configured_rules_file.read_text(encoding="utf-8"))
    assert persisted["user"] == updated_rule
    assert persisted["admin"] == _rules_payload()["admin"]
    assert persisted["dangerous_pattern"] == DANGEROUS_PATTERN


def test_rule_validation_rejects_unknown_missing_and_invalid_values(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    unknown_field = {**RULE_FIELDS, "rank": 1}
    assert client.put("/api/rbac-rules/user", headers=auth_headers, json=unknown_field).status_code == 422

    legacy_field = {**RULE_FIELDS, "allowed_tools": []}
    assert client.put("/api/rbac-rules/user", headers=auth_headers, json=legacy_field).status_code == 422

    missing_field = {key: value for key, value in RULE_FIELDS.items() if key != "denied_tools"}
    assert client.put("/api/rbac-rules/user", headers=auth_headers, json=missing_field).status_code == 422

    invalid_pattern = {**RULE_FIELDS, "tools_paras": {"terminal": [{"command": "["}]}}
    assert client.put("/api/rbac-rules/user", headers=auth_headers, json=invalid_pattern).status_code == 422

    assert client.put("/api/rbac-rules/unknown", headers=auth_headers, json=RULE_FIELDS).status_code == 422


def test_rule_file_rejects_missing_or_invalid_dangerous_pattern(
    client: TestClient,
    auth_headers: dict[str, str],
    configured_rules_file: Path,
) -> None:
    missing = _rules_payload()
    missing.pop("dangerous_pattern")
    configured_rules_file.write_text(json.dumps(missing), encoding="utf-8")
    assert client.get("/api/rbac-rules", headers=auth_headers).status_code == 422

    invalid = _rules_payload()
    invalid["dangerous_pattern"] = "["
    configured_rules_file.write_text(json.dumps(invalid), encoding="utf-8")
    assert client.get("/api/rbac-rules", headers=auth_headers).status_code == 422


def test_legacy_unknown_role_is_removed_and_persisted_atomically(tmp_path: Path) -> None:
    from aegis.backend.services.rbac_rule_service import RbacRuleService

    path = tmp_path / "legacy-role-rules.json"
    legacy_rules = _rules_payload()
    legacy_rules["unknown"] = {
        "summary": "Legacy fallback",
        "prompt_constraints": [],
        "allow_tools": ["rbac_status"],
        "denied_tools": [],
        "tools_paras": {},
    }
    path.write_text(json.dumps(legacy_rules), encoding="utf-8")

    rules = RbacRuleService(path).list_rules()

    assert set(rules) == {"admin", "operator", "user"}
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert set(persisted) == {
        "admin",
        "operator",
        "user",
        "dangerous_pattern",
    }
    assert persisted["user"] == legacy_rules["user"]


def test_legacy_single_parameter_maps_are_migrated_to_rule_lists(tmp_path: Path) -> None:
    from aegis.backend.services.rbac_rule_service import RbacRuleService

    path = tmp_path / "legacy-tool-rules.json"
    legacy_rules = _rules_payload()
    legacy_rules["user"]["tools_paras"] = {"write_file": {"path": "^/output/"}}
    path.write_text(json.dumps(legacy_rules), encoding="utf-8")

    rules = RbacRuleService(path).list_rules()

    assert rules["user"].tools_paras == {"write_file": [{"path": "^/output/"}]}
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["user"]["tools_paras"] == {"write_file": [{"path": "^/output/"}]}


def test_multiple_tool_parameter_rules_are_validated_and_persisted(
    client: TestClient,
    auth_headers: dict[str, str],
    configured_rules_file: Path,
) -> None:
    updated_rule = {
        **RULE_FIELDS,
        "tools_paras": {
            "write_file": [
                {"path": "^/output/"},
                {"path": r"(?<!\.secret)$"},
            ]
        },
    }

    updated = client.put("/api/rbac-rules/user", headers=auth_headers, json=updated_rule)

    assert updated.status_code == 200
    assert updated.json()["rule"] == updated_rule
    persisted = json.loads(configured_rules_file.read_text(encoding="utf-8"))
    assert persisted["user"] == updated_rule


def test_get_rejects_malformed_rule_file(
    client: TestClient,
    auth_headers: dict[str, str],
    configured_rules_file: Path,
) -> None:
    configured_rules_file.write_text("{not-json", encoding="utf-8")
    response = client.get("/api/rbac-rules", headers=auth_headers)
    assert response.status_code == 422
    assert response.json() == {"detail": "RBAC rules file is not valid JSON."}


def test_regex_test_uses_search_and_rejects_invalid_patterns(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    matched = client.post(
        "/api/rbac-rules/test",
        headers=auth_headers,
        json={"pattern": "ls", "text": "run ls -la"},
    )
    assert matched.status_code == 200
    assert matched.json() == {"matched": True}

    anchored = client.post(
        "/api/rbac-rules/test",
        headers=auth_headers,
        json={"pattern": "^ls", "text": "run ls -la"},
    )
    assert anchored.status_code == 200
    assert anchored.json() == {"matched": False}

    invalid = client.post(
        "/api/rbac-rules/test",
        headers=auth_headers,
        json={"pattern": "[", "text": "anything"},
    )
    assert invalid.status_code == 422
    assert invalid.json() == {"detail": "Invalid regular expression."}


def test_non_admin_cannot_access_rbac_rule_apis(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    created = client.post(
        "/api/users",
        headers=auth_headers,
        json={
            "username": "rules-reader",
            "password": "Password123!",
            "email": "rules-reader@example.com",
            "status": "enabled",
        },
    )
    assert created.status_code == 201
    logged_in = client.post(
        "/api/auth/login",
        json={"username": "rules-reader", "password": "Password123!"},
    )
    user_headers = {"Authorization": f"Bearer {logged_in.json()['access_token']}"}

    assert client.get("/api/rbac-rules", headers=user_headers).status_code == 403
    assert client.post(
        "/api/rbac-rules/test",
        headers=user_headers,
        json={"pattern": "x", "text": "x"},
    ).status_code == 403


def test_atomic_write_failure_keeps_the_previous_rules(
    monkeypatch: pytest.MonkeyPatch,
    configured_rules_file: Path,
) -> None:
    import aegis.backend.services.rbac_rule_service as service_module
    from aegis.backend.models import RbacRule
    from aegis.backend.services.rbac_rule_service import RbacRuleConfigError, RbacRuleService

    service = RbacRuleService(configured_rules_file)
    original = configured_rules_file.read_text(encoding="utf-8")
    monkeypatch.setattr(service_module.tempfile, "mkstemp", lambda **_kwargs: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(RbacRuleConfigError):
        service.update_rule("user", RbacRule.model_validate(RULE_FIELDS))

    assert configured_rules_file.read_text(encoding="utf-8") == original
