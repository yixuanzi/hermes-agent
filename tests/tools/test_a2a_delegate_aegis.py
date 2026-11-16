from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tools import a2a_delegate_aegis
from tools.a2a_delegate_aegis import (
    AegisDelegateStore,
    AgentPolicyConflictError,
    AgentPolicyNotFoundError,
    run_aegis_checked_delegate,
)


def test_schema_initialization_preserves_existing_users_table(tmp_path) -> None:
    db_path = tmp_path / "aegis.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE users (uid TEXT PRIMARY KEY, username TEXT NOT NULL)")
        conn.execute("INSERT INTO users (uid, username) VALUES ('u-1', 'alice')")
        conn.commit()

    AegisDelegateStore(db_path)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT uid, username FROM users").fetchall() == [("u-1", "alice")]
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert {"users", "policy", "a2a_delegate_audit"} <= tables


def test_policy_crud_normalizes_selectors_and_rejects_conflicts(tmp_path) -> None:
    store = AegisDelegateStore(tmp_path / "aegis.db")

    created = store.create_policy(
        rank_id=10,
        platform=" AEGIS ",
        user_id="",
        agent_name=" responder ",
        status="DENY",
    )
    assert created == {
        "rank_id": 10,
        "platform": "aegis",
        "user_id": "*",
        "agent_name": "responder",
        "status": "deny",
    }
    assert store.get_policy(10) == created

    updated = store.update_policy(
        10,
        rank_id=5,
        platform="*",
        user_id="alice",
        agent_name="responder",
        status="allow",
    )
    assert updated["rank_id"] == 5
    with pytest.raises(AgentPolicyNotFoundError):
        store.get_policy(10)

    store.create_policy(
        rank_id=8,
        platform="*",
        user_id="*",
        agent_name="*",
        status="deny",
    )
    with pytest.raises(AgentPolicyConflictError):
        store.update_policy(
            8,
            rank_id=5,
            platform="*",
            user_id="*",
            agent_name="*",
            status="deny",
        )

    store.delete_policy(5)
    with pytest.raises(AgentPolicyNotFoundError):
        store.delete_policy(5)


def test_lowest_rank_matching_policy_decides_and_no_match_allows(tmp_path) -> None:
    store = AegisDelegateStore(tmp_path / "aegis.db")
    store.create_policy(
        rank_id=20,
        platform="aegis",
        user_id="*",
        agent_name="responder",
        status="deny",
    )
    store.create_policy(
        rank_id=5,
        platform="aegis",
        user_id="alice",
        agent_name="responder",
        status="allow",
    )

    exempt = store.evaluate_policy(platform="AEGIS", user_id="alice", agent_name="responder")
    denied = store.evaluate_policy(platform="aegis", user_id="bob", agent_name="responder")
    unmatched = store.evaluate_policy(platform="slack", user_id="bob", agent_name="other")

    assert exempt.allowed is True
    assert exempt.matched_policy["rank_id"] == 5
    assert denied.allowed is False
    assert denied.matched_policy["rank_id"] == 20
    assert unmatched.allowed is True
    assert unmatched.matched_policy is None


def test_audit_query_supports_combined_filters_and_pagination(tmp_path) -> None:
    store = AegisDelegateStore(tmp_path / "aegis.db")
    common = {
        "platform": "aegis",
        "user_id": "u-1",
        "user_name": "Alice",
        "agent_name": "responder",
        "session_id": "remote-1",
        "is_loop": False,
        "is_delegate_output": True,
    }
    store.record_audit(
        **common,
        goal="Investigate phishing",
        status="succ",
        audit_id="audit-1",
        timestamp="2026-07-21T01:00:00.000000Z",
    )
    store.record_audit(
        **{**common, "user_id": "u-2", "user_name": "Bob"},
        goal="Investigate malware",
        status="fail",
        audit_id="audit-2",
        timestamp="2026-07-21T02:00:00.000000Z",
    )
    store.record_audit(
        **common,
        goal="Investigate phishing attachment",
        status="succ",
        audit_id="audit-3",
        timestamp="2026-07-21T03:00:00.000000Z",
    )

    filtered = store.query_audits(
        page=1,
        page_size=1,
        user_name="ali",
        goal="PHISHING",
        status="succ",
        is_delegate_output=True,
        timestamp_from="2026-07-21T00:00:00.000000Z",
        timestamp_to="2026-07-21T04:00:00.000000Z",
    )

    assert filtered.total == 2
    assert [row["id"] for row in filtered.logs] == ["audit-3"]
    assert filtered.logs[0]["is_loop"] is False
    assert filtered.logs[0]["is_delegate_output"] is True

    second_page = store.query_audits(
        page=2,
        page_size=1,
        user_name="ali",
        goal="phishing",
    )
    assert [row["id"] for row in second_page.logs] == ["audit-1"]


def test_overview_stats_uses_rolling_windows_and_ignores_blank_identities(tmp_path) -> None:
    store = AegisDelegateStore(tmp_path / "aegis.db")
    common = {
        "session_id": "session-1",
        "is_loop": False,
        "is_delegate_output": True,
    }
    store.record_audit(
        **common,
        platform="Slack",
        user_id="user-1",
        user_name="Alice",
        agent_name="agent-one",
        goal="Current start boundary",
        status="succ",
        audit_id="current-start",
        timestamp="2026-07-15T12:00:00.000000Z",
    )
    store.record_audit(
        **common,
        platform="slack",
        user_id="user-2",
        user_name="Bob",
        agent_name="agent-two",
        goal="Current failure",
        status="fail",
        audit_id="current-fail",
        timestamp="2026-07-16T12:00:00.000000Z",
    )
    store.record_audit(
        **common,
        platform="  ",
        user_id=" ",
        user_name="",
        agent_name=" ",
        goal="Denied with blank identities",
        status="auth_denied",
        audit_id="current-denied",
        timestamp="2026-07-17T12:00:00.000000Z",
    )
    store.record_audit(
        **common,
        platform="discord",
        user_id="user-3",
        user_name="Carol",
        agent_name="agent-three",
        goal="Previous success",
        status="succ",
        audit_id="previous-succ",
        timestamp="2026-07-10T12:00:00.000000Z",
    )
    store.record_audit(
        **common,
        platform="discord",
        user_id="user-3",
        user_name="Carol",
        agent_name="agent-three",
        goal="Previous failure",
        status="fail",
        audit_id="previous-fail",
        timestamp="2026-07-11T12:00:00.000000Z",
    )
    store.record_audit(
        **common,
        platform="teams",
        user_id="user-4",
        user_name="Dana",
        agent_name="agent-four",
        goal="Current end boundary is excluded",
        status="succ",
        audit_id="current-end",
        timestamp="2026-07-22T12:00:00.000000Z",
    )

    stats = store.get_overview_stats(now=datetime(2026, 7, 22, 12, tzinfo=UTC))

    assert stats["window_start"] == "2026-07-15T12:00:00.000000Z"
    assert stats["window_end"] == "2026-07-22T12:00:00.000000Z"
    assert stats["executing_agent_count"] == 2
    assert stats["source_platform_count"] == 1
    assert stats["active_user_count"] == 2
    assert stats["delegation_total"] == 3
    assert stats["success_count"] == 1
    assert stats["success_rate"] == pytest.approx(1 / 3)
    assert stats["status_counts"] == {"succ": 1, "fail": 1, "auth_denied": 1}
    assert stats["comparison"]["previous_delegation_total"] == 2
    assert stats["comparison"]["delegation_volume_change_percent"] == pytest.approx(50)
    assert stats["comparison"]["previous_success_rate"] == pytest.approx(0.5)
    assert stats["comparison"]["success_rate_change_percentage_points"] == pytest.approx(-100 / 6)


def test_overview_stats_returns_null_rates_without_delegations(tmp_path) -> None:
    stats = AegisDelegateStore(tmp_path / "aegis.db").get_overview_stats(
        now=datetime(2026, 7, 22, 12, tzinfo=UTC)
    )

    assert stats["delegation_total"] == 0
    assert stats["success_rate"] is None
    assert stats["comparison"] == {
        "previous_delegation_total": 0,
        "delegation_volume_change_percent": None,
        "previous_success_rate": None,
        "success_rate_change_percentage_points": None,
    }


def test_checked_delegate_returns_authorization_and_records_check_audit(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    parent = SimpleNamespace(
        platform="slack",
        _user_env_platform=None,
        _user_id="u-1",
        _user_name="Alice",
    )

    result = run_aegis_checked_delegate(
        parent_agent=parent,
        goal="Investigate",
        agent_name="responder",
        session_id="remote-1",
        is_loop=False,
        is_delegate_output=True,
    )

    assert result.allowed is True
    assert result.authorization == "allowed"
    assert result.failure_payload is None
    store = AegisDelegateStore(tmp_path / "aegis.db")
    audits = store.query_audits().logs
    assert len(audits) == 1
    assert audits[0]["status"] == "succ"


def test_checked_delegate_store_initialization_error_fails_closed(
    monkeypatch,
) -> None:
    def fail_store_initialization():
        raise OSError("database unavailable")

    monkeypatch.setattr(
        a2a_delegate_aegis,
        "get_aegis_delegate_store",
        fail_store_initialization,
    )

    result = run_aegis_checked_delegate(
        parent_agent=SimpleNamespace(platform="cli"),
        goal="Investigate",
        agent_name="responder",
        session_id=None,
        is_loop=False,
        is_delegate_output=True,
    )

    assert result.allowed is False
    assert result.authorization == "error"
    assert result.failure_payload is not None
    assert "database unavailable" not in result.failure_payload["error"]
