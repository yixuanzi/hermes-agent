"""Aegis authorization and audit storage for remote A2A delegation."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from hermes_constants import get_hermes_home


AEGIS_BOOTSTRAP_ADMIN_PASSWORD_ENV = "AEGIS_BOOTSTRAP_ADMIN_PASSWORD"
POLICY_STATUSES = frozenset({"allow", "deny"})
AUDIT_STATUSES = frozenset({"succ", "fail", "auth_denied"})

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS policy (
    rank_id INTEGER PRIMARY KEY CHECK(rank_id > 0),
    platform TEXT NOT NULL,
    user_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('allow', 'deny'))
);

CREATE INDEX IF NOT EXISTS idx_policy_match
    ON policy(platform, user_id, agent_name, rank_id);

CREATE TABLE IF NOT EXISTS a2a_delegate_audit (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    platform TEXT NOT NULL,
    user_id TEXT NOT NULL,
    user_name TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    goal TEXT NOT NULL,
    session_id TEXT NOT NULL,
    is_loop INTEGER NOT NULL CHECK(is_loop IN (0, 1)),
    is_delegate_output INTEGER NOT NULL CHECK(is_delegate_output IN (0, 1)),
    status TEXT NOT NULL CHECK(status IN ('succ', 'fail', 'auth_denied'))
);

CREATE INDEX IF NOT EXISTS idx_a2a_delegate_audit_timestamp
    ON a2a_delegate_audit(timestamp DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_a2a_delegate_audit_session
    ON a2a_delegate_audit(session_id);
"""


class AgentPolicyNotFoundError(LookupError):
    """Raised when an Agent Policy rank does not exist."""


class AgentPolicyConflictError(ValueError):
    """Raised when an Agent Policy rank already exists."""


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    matched_policy: dict[str, Any] | None = None


@dataclass(frozen=True)
class AegisDelegateCheckResult:
    """Authorization result returned to the A2A delegate tool."""

    allowed: bool
    authorization: Literal["allowed", "denied", "error"]
    failure_payload: dict[str, Any] | None


@dataclass(frozen=True)
class AuditPage:
    logs: list[dict[str, Any]]
    total: int
    page: int
    page_size: int


def is_aegis_delegate_security_enabled() -> bool:
    """Return whether Aegis check and audit are enabled for A2A delegation."""
    return bool((os.environ.get(AEGIS_BOOTSTRAP_ADMIN_PASSWORD_ENV) or "").strip())


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _selector(value: Any, *, lowercase: bool = False) -> str:
    normalized = str(value or "").strip() or "*"
    return normalized.lower() if lowercase else normalized


class AegisDelegateStore:
    """Small SQLite gateway shared by the tool and Aegis administration API."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (get_hermes_home() / "aegis.db")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _initialize(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()

    @staticmethod
    def normalize_policy(
        *,
        rank_id: int,
        platform: Any,
        user_id: Any,
        agent_name: Any,
        status: Any,
    ) -> dict[str, Any]:
        if isinstance(rank_id, bool) or not isinstance(rank_id, int) or rank_id <= 0:
            raise ValueError("rank_id must be a positive integer")
        normalized_status = str(status or "").strip().lower()
        if normalized_status not in POLICY_STATUSES:
            raise ValueError("status must be 'allow' or 'deny'")
        return {
            "rank_id": rank_id,
            "platform": _selector(platform, lowercase=True),
            "user_id": _selector(user_id),
            "agent_name": _selector(agent_name),
            "status": normalized_status,
        }

    def list_policies(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT rank_id, platform, user_id, agent_name, status "
                "FROM policy ORDER BY rank_id ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_policy(self, rank_id: int) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT rank_id, platform, user_id, agent_name, status "
                "FROM policy WHERE rank_id = ?",
                (rank_id,),
            ).fetchone()
        if row is None:
            raise AgentPolicyNotFoundError(rank_id)
        return dict(row)

    def create_policy(self, **values: Any) -> dict[str, Any]:
        policy = self.normalize_policy(**values)
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    "INSERT INTO policy (rank_id, platform, user_id, agent_name, status) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        policy["rank_id"],
                        policy["platform"],
                        policy["user_id"],
                        policy["agent_name"],
                        policy["status"],
                    ),
                )
                conn.commit()
        except sqlite3.IntegrityError as exc:
            raise AgentPolicyConflictError(policy["rank_id"]) from exc
        return policy

    def update_policy(self, current_rank_id: int, **values: Any) -> dict[str, Any]:
        policy = self.normalize_policy(**values)
        try:
            with self._lock, self._connect() as conn:
                cursor = conn.execute(
                    "UPDATE policy SET rank_id = ?, platform = ?, user_id = ?, "
                    "agent_name = ?, status = ? WHERE rank_id = ?",
                    (
                        policy["rank_id"],
                        policy["platform"],
                        policy["user_id"],
                        policy["agent_name"],
                        policy["status"],
                        current_rank_id,
                    ),
                )
                if cursor.rowcount == 0:
                    raise AgentPolicyNotFoundError(current_rank_id)
                conn.commit()
        except sqlite3.IntegrityError as exc:
            raise AgentPolicyConflictError(policy["rank_id"]) from exc
        return policy

    def delete_policy(self, rank_id: int) -> None:
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM policy WHERE rank_id = ?", (rank_id,))
            if cursor.rowcount == 0:
                raise AgentPolicyNotFoundError(rank_id)
            conn.commit()

    def evaluate_policy(self, *, platform: Any, user_id: Any, agent_name: Any) -> PolicyDecision:
        normalized_platform = str(platform or "").strip().lower()
        normalized_user_id = str(user_id or "").strip()
        normalized_agent_name = str(agent_name or "").strip()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT rank_id, platform, user_id, agent_name, status FROM policy "
                "WHERE (platform = '*' OR platform = ?) "
                "AND (user_id = '*' OR user_id = ?) "
                "AND (agent_name = '*' OR agent_name = ?) "
                "ORDER BY rank_id ASC LIMIT 1",
                (normalized_platform, normalized_user_id, normalized_agent_name),
            ).fetchone()
        if row is None:
            return PolicyDecision(allowed=True)
        matched = dict(row)
        return PolicyDecision(allowed=matched["status"] == "allow", matched_policy=matched)

    def record_audit(
        self,
        *,
        platform: Any,
        user_id: Any,
        user_name: Any,
        agent_name: Any,
        goal: Any,
        session_id: Any,
        is_loop: bool,
        is_delegate_output: bool,
        status: Literal["succ", "fail", "auth_denied"],
        audit_id: str | None = None,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        if status not in AUDIT_STATUSES:
            raise ValueError("invalid A2A audit status")
        record = {
            "id": audit_id or uuid.uuid4().hex,
            "timestamp": timestamp or _utc_timestamp(),
            "platform": str(platform or "").strip().lower(),
            "user_id": str(user_id or "").strip(),
            "user_name": str(user_name or "").strip(),
            "agent_name": str(agent_name or "").strip(),
            "goal": str(goal or ""),
            "session_id": str(session_id or "").strip(),
            "is_loop": bool(is_loop),
            "is_delegate_output": bool(is_delegate_output),
            "status": status,
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO a2a_delegate_audit "
                "(id, timestamp, platform, user_id, user_name, agent_name, goal, "
                "session_id, is_loop, is_delegate_output, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record["id"],
                    record["timestamp"],
                    record["platform"],
                    record["user_id"],
                    record["user_name"],
                    record["agent_name"],
                    record["goal"],
                    record["session_id"],
                    int(record["is_loop"]),
                    int(record["is_delegate_output"]),
                    record["status"],
                ),
            )
            conn.commit()
        return record

    def query_audits(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        timestamp_from: str | None = None,
        timestamp_to: str | None = None,
        is_loop: bool | None = None,
        is_delegate_output: bool | None = None,
        status: str | None = None,
        **text_filters: Any,
    ) -> AuditPage:
        if page < 1 or page_size < 1 or page_size > 100:
            raise ValueError("invalid audit page")

        allowed_text_fields = {
            "id",
            "platform",
            "user_id",
            "user_name",
            "agent_name",
            "goal",
            "session_id",
        }
        clauses: list[str] = []
        params: list[Any] = []
        for field in sorted(allowed_text_fields):
            value = str(text_filters.get(field) or "").strip()
            if value:
                clauses.append(f"instr(lower({field}), lower(?)) > 0")
                params.append(value)
        if status:
            normalized_status = str(status).strip().lower()
            if normalized_status not in AUDIT_STATUSES:
                raise ValueError("invalid A2A audit status")
            clauses.append("status = ?")
            params.append(normalized_status)
        if is_loop is not None:
            clauses.append("is_loop = ?")
            params.append(int(is_loop))
        if is_delegate_output is not None:
            clauses.append("is_delegate_output = ?")
            params.append(int(is_delegate_output))
        if timestamp_from:
            clauses.append("timestamp >= ?")
            params.append(timestamp_from)
        if timestamp_to:
            clauses.append("timestamp <= ?")
            params.append(timestamp_to)

        where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        offset = (page - 1) * page_size
        with self._lock, self._connect() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM a2a_delegate_audit{where_sql}",
                    params,
                ).fetchone()[0]
            )
            rows = conn.execute(
                "SELECT id, timestamp, platform, user_id, user_name, agent_name, goal, "
                "session_id, is_loop, is_delegate_output, status "
                f"FROM a2a_delegate_audit{where_sql} "
                "ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?",
                [*params, page_size, offset],
            ).fetchall()

        logs = []
        for row in rows:
            record = dict(row)
            record["is_loop"] = bool(record["is_loop"])
            record["is_delegate_output"] = bool(record["is_delegate_output"])
            logs.append(record)
        return AuditPage(logs=logs, total=total, page=page, page_size=page_size)

    def get_overview_stats(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Return aggregate A2A delegation metrics for the current and prior seven-day windows."""
        window_end = now or datetime.now(UTC)
        if window_end.tzinfo is None:
            window_end = window_end.replace(tzinfo=UTC)
        window_end = window_end.astimezone(UTC)
        window_start = window_end - timedelta(days=7)
        previous_start = window_start - timedelta(days=7)

        with self._lock, self._connect() as conn:
            current = self._aggregate_audits(conn, window_start, window_end)
            previous = self._aggregate_audits(conn, previous_start, window_start)

        success_rate = self._success_rate(current)
        previous_success_rate = self._success_rate(previous)
        previous_total = previous["delegation_total"]
        volume_change = (
            ((current["delegation_total"] - previous_total) / previous_total) * 100
            if previous_total
            else None
        )
        success_rate_change = (
            (success_rate - previous_success_rate) * 100
            if success_rate is not None and previous_success_rate is not None
            else None
        )

        return {
            "window_start": self._format_utc_timestamp(window_start),
            "window_end": self._format_utc_timestamp(window_end),
            "executing_agent_count": current["executing_agent_count"],
            "source_platform_count": current["source_platform_count"],
            "active_user_count": current["active_user_count"],
            "delegation_total": current["delegation_total"],
            "success_count": current["succ"],
            "success_rate": success_rate,
            "status_counts": {
                "succ": current["succ"],
                "fail": current["fail"],
                "auth_denied": current["auth_denied"],
            },
            "comparison": {
                "previous_delegation_total": previous_total,
                "delegation_volume_change_percent": volume_change,
                "previous_success_rate": previous_success_rate,
                "success_rate_change_percentage_points": success_rate_change,
            },
        }

    @staticmethod
    def _aggregate_audits(
        conn: sqlite3.Connection,
        window_start: datetime,
        window_end: datetime,
    ) -> dict[str, int]:
        row = conn.execute(
            "SELECT "
            "COUNT(*) AS delegation_total, "
            "COUNT(DISTINCT CASE WHEN TRIM(agent_name) <> '' THEN TRIM(agent_name) END) "
            "AS executing_agent_count, "
            "COUNT(DISTINCT CASE WHEN TRIM(platform) <> '' THEN LOWER(TRIM(platform)) END) "
            "AS source_platform_count, "
            "COUNT(DISTINCT CASE WHEN TRIM(user_id) <> '' THEN TRIM(user_id) END) "
            "AS active_user_count, "
            "COALESCE(SUM(CASE WHEN status = 'succ' THEN 1 ELSE 0 END), 0) AS succ, "
            "COALESCE(SUM(CASE WHEN status = 'fail' THEN 1 ELSE 0 END), 0) AS fail, "
            "COALESCE(SUM(CASE WHEN status = 'auth_denied' THEN 1 ELSE 0 END), 0) "
            "AS auth_denied "
            "FROM a2a_delegate_audit WHERE timestamp >= ? AND timestamp < ?",
            (
                AegisDelegateStore._format_utc_timestamp(window_start),
                AegisDelegateStore._format_utc_timestamp(window_end),
            ),
        ).fetchone()
        return {key: int(row[key]) for key in row.keys()}

    @staticmethod
    def _success_rate(aggregate: dict[str, int]) -> float | None:
        total = aggregate["delegation_total"]
        return aggregate["succ"] / total if total else None

    @staticmethod
    def _format_utc_timestamp(value: datetime) -> str:
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


_STORE_CACHE: dict[Path, AegisDelegateStore] = {}
_STORE_CACHE_LOCK = threading.Lock()


def get_aegis_delegate_store(path: Path | None = None) -> AegisDelegateStore:
    """Return one initialized store per resolved database path in this process."""
    resolved_path = (path or (get_hermes_home() / "aegis.db")).resolve()
    store = _STORE_CACHE.get(resolved_path)
    if store is not None:
        return store
    with _STORE_CACHE_LOCK:
        store = _STORE_CACHE.get(resolved_path)
        if store is None:
            store = AegisDelegateStore(resolved_path)
            _STORE_CACHE[resolved_path] = store
    return store


def _delegate_identity(parent_agent: Any) -> dict[str, str]:
    return {
        "platform": str(
            getattr(parent_agent, "_user_env_platform", None)
            or getattr(parent_agent, "platform", "")
            or ""
        ).strip().lower(),
        "user_id": str(getattr(parent_agent, "_user_id", "") or "").strip(),
        "user_name": str(getattr(parent_agent, "_user_name", "") or "").strip(),
    }


def _authorization_failure_payload(
    *,
    goal: str,
    agent_name: str,
    session_id: str,
    authorization: Literal["denied", "error"],
    error: str,
) -> dict[str, Any]:
    return {
        "success": False,
        "type": "a2a",
        "agent_name": agent_name,
        "goal": goal,
        "session_id": session_id,
        "authorization": authorization,
        "error": error,
    }


def _record_delegate_audit(
    store: AegisDelegateStore | None,
    *,
    identity: dict[str, str],
    goal: str,
    agent_name: str,
    session_id: str,
    is_loop: bool,
    is_delegate_output: bool,
    status: Literal["succ", "fail"],
) -> None:
    if store is None:
        return
    try:
        store.record_audit(
            **identity,
            goal=goal,
            agent_name=agent_name,
            session_id=session_id,
            is_loop=is_loop,
            is_delegate_output=is_delegate_output,
            status=status,
        )
    except Exception:
        logger.exception("Failed to write Aegis A2A delegate audit record")


def run_aegis_checked_delegate(
    *,
    parent_agent: Any,
    goal: Any,
    agent_name: Any,
    session_id: Any,
    is_loop: bool,
    is_delegate_output: bool,
) -> AegisDelegateCheckResult:
    """Check authorization without owning or invoking remote delegation."""
    identity = _delegate_identity(parent_agent)
    normalized_goal = str(goal or "").strip()
    normalized_agent_name = str(agent_name or "").strip()
    normalized_session_id = str(session_id or "").strip()
    audit_values = {
        "identity": identity,
        "goal": normalized_goal,
        "agent_name": normalized_agent_name,
        "session_id": normalized_session_id,
        "is_loop": bool(is_loop),
        "is_delegate_output": bool(is_delegate_output),
    }

    store: AegisDelegateStore | None = None
    try:
        store = get_aegis_delegate_store()
        decision = store.evaluate_policy(
            platform=identity["platform"],
            user_id=identity["user_id"],
            agent_name=normalized_agent_name,
        )
    except Exception:
        logger.exception("Aegis Agent Policy check failed; denying A2A delegation")
        result = AegisDelegateCheckResult(
            allowed=False,
            authorization="error",
            failure_payload=_authorization_failure_payload(
                goal=normalized_goal,
                agent_name=normalized_agent_name,
                session_id=normalized_session_id,
                authorization="error",
                error="Aegis Agent Policy check failed; delegation denied.",
            ),
        )
        _record_delegate_audit(store, status="fail", **audit_values)
        return result

    if not decision.allowed:
        matched_rank = (decision.matched_policy or {}).get("rank_id")
        logger.warning(
            "Aegis Agent Policy denied A2A delegation: platform=%s user_id=%s "
            "agent_name=%s rank_id=%s",
            identity["platform"],
            identity["user_id"],
            normalized_agent_name,
            matched_rank,
        )
        result = AegisDelegateCheckResult(
            allowed=False,
            authorization="denied",
            failure_payload=_authorization_failure_payload(
                goal=normalized_goal,
                agent_name=normalized_agent_name,
                session_id=normalized_session_id,
                authorization="denied",
                error="Aegis Agent Policy denied A2A delegation.",
            ),
        )
        _record_delegate_audit(store, status="fail", **audit_values)
        return result

    result = AegisDelegateCheckResult(
        allowed=True,
        authorization="allowed",
        failure_payload=None,
    )
    _record_delegate_audit(store, status="succ", **audit_values)
    return result
