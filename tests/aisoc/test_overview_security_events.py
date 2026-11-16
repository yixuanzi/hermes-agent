"""Tests for aisoc.backend overview_service.list_security_events.

Covers the merged security-events feed: cron sessions (type from job_id) plus
recent (<3 day) non-cron sessions that hit a security keyword, sorted by time.
"""

import time

import pytest

import hermes_state
from hermes_state import SessionDB
from aisoc.backend.services import overview_service


@pytest.fixture()
def overview_db(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", db_path)
    db = SessionDB(db_path=db_path)
    yield db
    db.close()


def _insert_session(db, sid, source, started_at, *, title=None, ended_at=None, input_tokens=0, output_tokens=0):
    db._conn.execute(
        """
        INSERT INTO sessions (id, source, started_at, ended_at, title, input_tokens, output_tokens)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (sid, source, started_at, ended_at, title, input_tokens, output_tokens),
    )


def _insert_message(db, session_id, role, content, timestamp):
    db._conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        (session_id, role, content, timestamp),
    )


def test_cron_event_included_with_job_type(overview_db):
    now = time.time()
    _insert_session(overview_db, "cron_f721eacc24df_1", "cron", now)
    _insert_message(overview_db, "cron_f721eacc24df_1", "assistant", "扫描完成，风险评分 85/100，发现严重问题", now + 1)

    events = overview_service.list_security_events(15)

    assert len(events) == 1
    ev = events[0]
    assert ev["session_id"] == "cron_f721eacc24df_1"
    assert ev["type_label"] == "漏洞追踪"  # f721eacc24df -> vuln_tracking
    assert ev["risk_level"] == "Critical"


def test_recent_session_investigation_included(overview_db):
    now = time.time()
    _insert_session(overview_db, "cli_a", "cli", now)
    _insert_message(overview_db, "cli_a", "assistant", "已排查 CVE-2026-1234，判断为 high 高危漏洞", now + 1)

    events = overview_service.list_security_events(15)

    assert len(events) == 1
    ev = events[0]
    assert ev["session_id"] == "cli_a"
    assert ev["type_label"] == "会话调查"
    assert ev["type"] == "session_investigation"
    assert ev["risk_level"] == "High"
    assert "CVE-2026-1234" in ev["entities"]


def test_recent_session_matches_keyword_in_title(overview_db):
    now = time.time()
    _insert_session(overview_db, "cli_title", "cli", now, title="钓鱼邮件应急响应")
    _insert_message(overview_db, "cli_title", "assistant", "处理完毕", now + 1)

    events = overview_service.list_security_events(15)

    assert [e["session_id"] for e in events] == ["cli_title"]


def test_non_security_recent_session_excluded(overview_db):
    now = time.time()
    _insert_session(overview_db, "cli_dev", "cli", now, title="写个排序函数")
    _insert_message(overview_db, "cli_dev", "user", "帮我用 python 写一个快速排序", now)
    _insert_message(overview_db, "cli_dev", "assistant", "好的，这是实现", now + 1)

    events = overview_service.list_security_events(15)

    assert events == []


def test_old_security_session_excluded(overview_db):
    now = time.time()
    old = now - 4 * 86400  # older than the 3-day window
    _insert_session(overview_db, "cli_old", "cli", old)
    _insert_message(overview_db, "cli_old", "assistant", "分析了 CVE-2026-9999 漏洞", old + 1)

    events = overview_service.list_security_events(15)

    assert events == []


def test_merge_sorted_by_time_desc(overview_db):
    now = time.time()
    # cron event older, cli investigation newer
    _insert_session(overview_db, "cron_614ad4c64bdb_x", "cron", now - 100)
    _insert_message(overview_db, "cron_614ad4c64bdb_x", "assistant", "漏洞研判完成", now - 99)
    _insert_session(overview_db, "cli_new", "cli", now)
    _insert_message(overview_db, "cli_new", "assistant", "发现 malware 恶意样本", now + 1)

    events = overview_service.list_security_events(15)

    assert [e["session_id"] for e in events] == ["cli_new", "cron_614ad4c64bdb_x"]


def test_limit_clamped(overview_db):
    now = time.time()
    for i in range(3):
        sid = f"cli_{i}"
        _insert_session(overview_db, sid, "cli", now + i)
        _insert_message(overview_db, sid, "assistant", f"攻击事件 {i}", now + i + 0.5)

    events = overview_service.list_security_events(1)

    assert len(events) == 1
    assert events[0]["session_id"] == "cli_2"  # newest
