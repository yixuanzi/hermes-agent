"""Aegis-side reader and writer for the RBAC role rules JSON file."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from aegis.backend.models import RbacRule, RbacRuleRole


RBAC_RULES_ENV = "AEGIS_RBAC_RULES_PATH"
DEFAULT_RBAC_RULES_PATH = Path("plugins/rbac-guard/role_rules.json")
RBAC_ROLES: tuple[RbacRuleRole, ...] = ("admin", "operator", "user")
_LEGACY_UNKNOWN_ROLE = "unknown"
RBAC_RULE_FIELDS = frozenset(
    {"summary", "prompt_constraints", "allow_tools", "denied_tools", "tools_paras"}
)
DANGEROUS_PATTERN_FIELD = "dangerous_pattern"
_RULES_LOCK = threading.RLock()


class RbacRuleConfigError(ValueError):
    """Raised when the configured RBAC rules file cannot be used."""


def resolve_rbac_rules_path() -> Path:
    """Resolve the configured rules path without depending on HERMES_HOME."""
    configured = (os.environ.get(RBAC_RULES_ENV) or "").strip()
    path = Path(configured) if configured else DEFAULT_RBAC_RULES_PATH
    if path.is_absolute():
        return path

    # This file lives at <hermes-root>/aegis/backend/services/..., so relative
    # RBAC paths are stable even when Aegis changes cwd to HERMES_HOME.
    hermes_root = Path(__file__).resolve().parents[3]
    return hermes_root / path


class RbacRuleService:
    """Validate and atomically update the complete three-role rule map."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or resolve_rbac_rules_path()).resolve()

    def list_rules(self) -> dict[RbacRuleRole, RbacRule]:
        with _RULES_LOCK:
            raw_rules, migrated = self._read_rules_locked()
            validated = self._validated_rules(raw_rules)
            if migrated:
                self._write_rules_locked(raw_rules)
            return validated

    def update_rule(self, role: str, rule: RbacRule) -> RbacRule:
        if role not in RBAC_ROLES:
            raise RbacRuleConfigError("Unsupported RBAC role.")

        with _RULES_LOCK:
            raw_rules, _migrated = self._read_rules_locked()
            # Validate the incoming Pydantic instance once more at the storage
            # boundary, then preserve every other role's JSON value.
            validated = self._validate_rule(role, rule.model_dump(mode="json"))
            raw_rules[role] = validated.model_dump(mode="json")
            self._write_rules_locked(raw_rules)
            return validated

    @staticmethod
    def test_pattern(pattern: str, text: str) -> bool:
        try:
            return re.search(pattern, text) is not None
        except re.error as exc:
            raise RbacRuleConfigError("Invalid regular expression.") from exc

    def _read_rules_locked(self) -> tuple[dict[str, Any], bool]:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError as exc:
            raise RbacRuleConfigError("RBAC rules file was not found.") from exc
        except (OSError, UnicodeError) as exc:
            raise RbacRuleConfigError("RBAC rules file could not be read.") from exc
        except json.JSONDecodeError as exc:
            raise RbacRuleConfigError("RBAC rules file is not valid JSON.") from exc

        if not isinstance(payload, dict):
            raise RbacRuleConfigError("RBAC rules must be a JSON object.")
        expected_roles = set(RBAC_ROLES)
        configured_roles = set(payload)
        expected_fields = expected_roles | {DANGEROUS_PATTERN_FIELD}
        if configured_roles == expected_fields | {_LEGACY_UNKNOWN_ROLE}:
            payload.pop(_LEGACY_UNKNOWN_ROLE)
            migrated = True
        elif configured_roles != expected_fields:
            raise RbacRuleConfigError(
                "RBAC rules must define the three supported roles and dangerous_pattern."
            )
        else:
            migrated = False
        dangerous_pattern = payload[DANGEROUS_PATTERN_FIELD]
        if not isinstance(dangerous_pattern, str):
            raise RbacRuleConfigError("RBAC dangerous_pattern must be a string.")
        try:
            re.compile(dangerous_pattern)
        except re.error as exc:
            raise RbacRuleConfigError("RBAC dangerous_pattern is not a valid regular expression.") from exc
        for role in RBAC_ROLES:
            role_payload = payload[role]
            if not isinstance(role_payload, dict):
                continue
            tools_paras = role_payload.get("tools_paras")
            if not isinstance(tools_paras, dict):
                continue
            normalized_tools_paras: dict[str, Any] = {}
            for tool_name, rules in tools_paras.items():
                if isinstance(rules, dict):
                    normalized_tools_paras[tool_name] = [rules]
                    migrated = True
                else:
                    normalized_tools_paras[tool_name] = rules
            if migrated and normalized_tools_paras != tools_paras:
                role_payload["tools_paras"] = normalized_tools_paras
        return payload, migrated

    def _validated_rules(self, raw_rules: dict[str, Any]) -> dict[RbacRuleRole, RbacRule]:
        return {
            role: self._validate_rule(role, raw_rules[role])
            for role in RBAC_ROLES
        }

    @staticmethod
    def _validate_rule(role: str, raw_rule: Any) -> RbacRule:
        if not isinstance(raw_rule, dict):
            raise RbacRuleConfigError(f"RBAC rule for {role} must be a JSON object.")
        if set(raw_rule) != RBAC_RULE_FIELDS:
            raise RbacRuleConfigError(
                f"RBAC rule for {role} must contain only the complete supported fields."
            )
        try:
            rule = RbacRule.model_validate(raw_rule)
        except ValidationError as exc:
            raise RbacRuleConfigError(f"RBAC rule for {role} failed validation.") from exc

        for tool_rules in rule.tools_paras.values():
            for parameter_rule in tool_rules:
                for pattern in parameter_rule.values():
                    try:
                        re.compile(pattern)
                    except re.error as exc:
                        raise RbacRuleConfigError(
                            f"RBAC rule for {role} contains an invalid regular expression."
                        ) from exc
        return rule

    def _write_rules_locked(self, raw_rules: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            fd, temporary_name = tempfile.mkstemp(
                dir=str(self.path.parent),
                prefix=f".{self.path.name}.",
                suffix=".tmp",
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(raw_rules, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary_path.replace(self.path)
        except OSError as exc:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise RbacRuleConfigError("RBAC rules file could not be written.") from exc
