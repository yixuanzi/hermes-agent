"""RBAC 角色矩阵：身份 → 角色，角色 → 权限。

身份键 = f"{platform}:{user_id}"（小写）。角色身份映射来自 Aegis 的
user_roles 表；未登记用户按 user 角色处理。
"""

import atexit
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from hermes_constants import get_hermes_home


# ---------------------------------------------------------------- 数据库存储 ----
# user_roles is created by Aegis. The IF NOT EXISTS fallback keeps the plugin
# usable when it is loaded before the Aegis server has initialized its schema.
_ROLE_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_roles (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    uid TEXT NOT NULL,
    uname TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'operator', 'admin')),
    update_time TEXT NOT NULL,
    UNIQUE(platform, uid)
)
"""
_ROLE_DB_PATH = Path(
    os.environ.get("AEGIS_DB_PATH", str(get_hermes_home() / "aegis.db"))
)
_AUDIT_PATH = Path(str(Path(__file__).parent / "data" / "audit.log"))

_AUDIT_ENABLED = (os.environ.get("AEGIS_RBAC_AUDIT") or "").strip().lower() == "true"

_audit_lock = threading.Lock()
_role_db_lock = threading.RLock()
_role_db: sqlite3.Connection | None = None

PERSISTED_ROLES = ("user", "operator", "admin")
_ROLE_RULE_FIELDS = frozenset(
    {"summary", "prompt_constraints", "allow_tools", "denied_tools", "tools_paras"}
)
_DANGEROUS_PATTERN_FIELD = "dangerous_pattern"
_ROLE_RULES_ENV = "AEGIS_RBAC_RULES_PATH"
_LEGACY_UNKNOWN_ROLE = "unknown"


def _resolve_role_rules_path() -> Path:
    configured = (os.environ.get(_ROLE_RULES_ENV) or "").strip()
    path = Path(configured) if configured else Path("plugins/rbac-guard/role_rules.json")
    if path.is_absolute():
        return path
    # roles.py is <hermes-root>/plugins/rbac-guard/roles.py for both the
    # checkout and the standard ~/.hermes/plugins installation layout.
    return Path(__file__).resolve().parents[2] / path


_ROLE_RULES_PATH = _resolve_role_rules_path()


class RoleRulesConfigError(ValueError):
    """Raised when the plugin's role_rules.json is missing or invalid."""


def _load_role_rules_config(path: Path | None = None) -> tuple[dict[str, dict], re.Pattern[str]]:
    path = (path or _resolve_role_rules_path()).resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RoleRulesConfigError(f"Unable to load {path.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RoleRulesConfigError(f"{path.name} must be a JSON object")
    configured_roles = set(payload)
    expected_roles = set(PERSISTED_ROLES)
    expected_fields = expected_roles | {_DANGEROUS_PATTERN_FIELD}
    migrated = configured_roles == expected_fields | {_LEGACY_UNKNOWN_ROLE}
    if migrated:
        payload.pop(_LEGACY_UNKNOWN_ROLE)
    elif configured_roles != expected_fields:
        raise RoleRulesConfigError(
            f"{path.name} must define exactly: {', '.join(PERSISTED_ROLES)}, "
            f"{_DANGEROUS_PATTERN_FIELD}"
        )

    dangerous_pattern = payload[_DANGEROUS_PATTERN_FIELD]
    if not isinstance(dangerous_pattern, str):
        raise RoleRulesConfigError(
            f"{_DANGEROUS_PATTERN_FIELD} must be a regular expression string"
        )
    try:
        compiled_dangerous_pattern = re.compile(dangerous_pattern, re.I)
    except re.error as exc:
        raise RoleRulesConfigError(
            f"Invalid regex for {_DANGEROUS_PATTERN_FIELD}: {exc}"
        ) from exc

    validated: dict[str, dict] = {}
    for role_name in PERSISTED_ROLES:
        raw = payload[role_name]
        if not isinstance(raw, dict) or set(raw) != _ROLE_RULE_FIELDS:
            raise RoleRulesConfigError(
                f"Role {role_name!r} must contain exactly: "
                f"{', '.join(sorted(_ROLE_RULE_FIELDS))}"
            )
        summary = raw["summary"]
        prompt_constraints = raw["prompt_constraints"]
        allow_tools = raw["allow_tools"]
        denied_tools = raw["denied_tools"]
        tools_paras = raw["tools_paras"]
        if not isinstance(summary, str):
            raise RoleRulesConfigError(f"Role {role_name!r} summary must be a string")
        if not isinstance(prompt_constraints, list) or any(
            not isinstance(item, str) for item in prompt_constraints
        ):
            raise RoleRulesConfigError(
                f"Role {role_name!r} prompt_constraints must be an array of strings"
            )
        if allow_tools is not None and (
            not isinstance(allow_tools, list)
            or any(not isinstance(item, str) or not item for item in allow_tools)
        ):
            raise RoleRulesConfigError(
                f"Role {role_name!r} allow_tools must be null or an array of strings"
            )
        if not isinstance(denied_tools, list) or any(
            not isinstance(item, str) or not item for item in denied_tools
        ):
            raise RoleRulesConfigError(
                f"Role {role_name!r} denied_tools must be an array of strings"
            )
        if not isinstance(tools_paras, dict):
            raise RoleRulesConfigError(
                f"Role {role_name!r} tools_paras must be an object"
            )
        normalized_tools_paras: dict[str, list[dict[str, str]]] = {}
        for tool_name, raw_rules in tools_paras.items():
            if not isinstance(tool_name, str) or not tool_name:
                raise RoleRulesConfigError("tools_paras tool names must be non-empty strings")
            parameter_rules_list = raw_rules
            if isinstance(raw_rules, dict):
                parameter_rules_list = [raw_rules]
                migrated = True
            if not isinstance(parameter_rules_list, list) or any(
                not isinstance(parameter_rules, dict)
                or any(
                    not isinstance(parameter_name, str)
                    or not parameter_name
                    or not isinstance(pattern, str)
                    for parameter_name, pattern in parameter_rules.items()
                )
                for parameter_rules in parameter_rules_list
            ):
                raise RoleRulesConfigError(
                    f"Role {role_name!r} tools_paras[{tool_name!r}] must be an array of parameter maps with regex strings"
                )
            for parameter_rules in parameter_rules_list:
                for parameter_name, pattern in parameter_rules.items():
                    try:
                        re.compile(pattern)
                    except re.error as exc:
                        raise RoleRulesConfigError(
                            f"Invalid regex for {role_name}.{tool_name}.{parameter_name}: {exc}"
                        ) from exc
            normalized_tools_paras[tool_name] = [dict(parameter_rules) for parameter_rules in parameter_rules_list]
        raw["tools_paras"] = normalized_tools_paras
        validated[role_name] = {
            "summary": summary,
            "prompt_constraints": list(prompt_constraints),
            "allow_tools": None if allow_tools is None else list(allow_tools),
            "denied_tools": list(denied_tools),
            "tools_paras": normalized_tools_paras,
        }
    if migrated:
        _write_role_rules_atomically(
            path,
            {_DANGEROUS_PATTERN_FIELD: dangerous_pattern, **validated},
        )
    return validated, compiled_dangerous_pattern


def _load_role_rules(path: Path | None = None) -> dict[str, dict]:
    """Load and validate the role-specific section of role_rules.json."""
    return _load_role_rules_config(path)[0]


def _write_role_rules_atomically(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        fd, temporary_name = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(path)
    except OSError as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise RoleRulesConfigError(f"Unable to migrate {path.name}: {exc}") from exc


# Loaded once at plugin import/startup. User role assignments remain dynamic in SQLite.
ROLE_RULES, DANGEROUS_PATTERN = _load_role_rules_config()


# ------------------------------------------------------------------ 工具函数 ----
def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _role_db_connection() -> sqlite3.Connection:
    """Return the process-wide role DB connection.

    SQLite connections are safe to share across hook threads here because all
    operations are serialized by ``_role_db_lock`` and the connection opts out
    of SQLite's same-thread restriction. Autocommit keeps every lookup fresh,
    so changes made by the Aegis Users page are visible on the next hook call.
    """
    global _role_db
    if _role_db is not None:
        return _role_db
    with _role_db_lock:
        if _role_db is None:
            _ROLE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                str(_ROLE_DB_PATH),
                timeout=5.0,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.executescript(_ROLE_DB_SCHEMA)
            _role_db = connection
    return _role_db


def close_role_db() -> None:
    """Close the persistent connection during interpreter shutdown or tests."""
    global _role_db
    with _role_db_lock:
        if _role_db is not None:
            _role_db.close()
            _role_db = None


atexit.register(close_role_db)


def _role_record(platform: str, user_id: str) -> sqlite3.Row | None:
    normalized_platform = (platform or "cli").strip().lower()
    normalized_uid = (user_id or "local").strip()
    with _role_db_lock:
        return _role_db_connection().execute(
            "SELECT id, platform, uid, uname, role, update_time "
            "FROM user_roles WHERE lower(platform) = ? AND uid = ?",
            (normalized_platform, normalized_uid),
        ).fetchone()


def audit(event: str, **fields) -> None:
    """追加审计日志（JSONL）。这是安全事件的唯一事实来源。"""
    if not _AUDIT_ENABLED:
        return
    try:
        with _audit_lock:
            _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
            rec = {"ts": round(time.time(), 3), "event": event, **fields}
            with _AUDIT_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 审计失败绝不阻断主流程


def identity_key(platform: str, user_id: str) -> str:
    return f"{(platform or 'cli').strip().lower()}:{(user_id or 'local').strip()}"


def role_for(platform: str, user_id: str) -> str:
    """Resolve identity to the current role in the Aegis database."""
    key = identity_key(platform, user_id)
    row = _role_record(platform, user_id)
    hit = row["role"] if row is not None else None
    if hit in PERSISTED_ROLES:
        return hit
    if hit is not None:
        audit("invalid_role_in_database", identity=key, value=str(hit))
    return "user"


def get_role(platform: str, user_id: str) -> dict:
    return ROLE_RULES[role_for(platform, user_id)]


def set_role(platform: str, user_id: str, role: str) -> bool:
    """Create or update a role record; the next lookup sees it immediately."""
    if role not in PERSISTED_ROLES:
        return False
    normalized_platform = (platform or "cli").strip().lower()
    normalized_uid = (user_id or "local").strip()
    now = _utc_timestamp()
    with _role_db_lock:
        connection = _role_db_connection()
        existing = connection.execute(
            "SELECT id, uname FROM user_roles "
            "WHERE lower(platform) = ? AND uid = ?",
            (normalized_platform, normalized_uid),
        ).fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO user_roles "
                "(id, platform, uid, uname, role, update_time) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), normalized_platform, normalized_uid, normalized_uid, role, now),
            )
        else:
            connection.execute(
                "UPDATE user_roles SET role = ?, update_time = ? WHERE id = ?",
                (role, now, existing["id"]),
            )
    audit("role_set", identity=identity_key(platform, user_id), role=role)
    return True


def tool_allowed(role: dict, tool_name: str) -> bool:
    """代码层硬控制：denied_tools 黑名单优先，其次 allow_tools 白名单。"""
    if tool_name in role.get("denied_tools", []):
        return False
    allowed = role.get("allow_tools")
    if allowed is not None:
        return tool_name in allowed
    return True


def _parameter_text(value: object) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)


def tool_params_allowed(
    role: Mapping[str, object],
    tool_name: str,
    args: Mapping[str, object] | None,
) -> tuple[bool, str]:
    """Check all configured parameter regexes for a tool.

    ``tools_paras`` is intentionally an allow rule: when a tool is listed,
    every configured parameter must exist and match its regex. A tool absent
    from the mapping has no additional parameter restriction.
    """
    tools_paras = role.get("tools_paras", {})
    if not isinstance(tools_paras, Mapping) or tool_name not in tools_paras:
        return True, ""
    parameter_rules_list = tools_paras[tool_name]
    if not isinstance(parameter_rules_list, list):  # validated at startup
        return False, "invalid parameter rule"
    for rule_index, parameter_rules in enumerate(parameter_rules_list, start=1):
        if not isinstance(parameter_rules, Mapping):  # validated at startup
            return False, f"invalid parameter rule {rule_index}"
        for parameter_name, pattern in parameter_rules.items():
            if not isinstance(args, Mapping) or parameter_name not in args:
                return False, f"missing parameter {parameter_name!r} in rule {rule_index}"
            value = _parameter_text(args[parameter_name])
            if re.search(str(pattern), value) is None:
                return False, f"parameter {parameter_name!r} does not match rule {rule_index}"
    return True, ""


def prompt_block(platform: str, user_id: str) -> str:
    """生成插入到本轮 user message 的角色约束块（软约束层）。"""
    name = role_for(platform, user_id)
    r = ROLE_RULES[name]
    lines = [
        "<rbac_context source=\"rbac-guard plugin\" enforce=\"hard\">",
        f"当前用户身份: {identity_key(platform, user_id)};",
        f"当前用户角色: {name} — {r['summary']};",
        "以下约束由 RBAC 插件在代码层强制执行，与你的行为必须一致；",
    ]
    lines += [f"- {c}" for c in r["prompt_constraints"]]
    allowed = r.get("allow_tools")
    if allowed is not None:
        lines.append("- 可用工具白名单: " + ", ".join(sorted(allowed)))
    else:
        denied = r.get("denied_tools", [])
        if denied:
            lines.append("- 禁用工具: " + ", ".join(sorted(denied)))
    lines.append("</rbac_context>")
    return "\n".join(lines)
