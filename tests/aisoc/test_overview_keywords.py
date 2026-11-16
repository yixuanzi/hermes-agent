"""Tests for aisoc.backend overview_service.list_keywords.

The keyword cloud now surfaces ONLY security-relevant terms: it counts hits of
the curated _SECURITY_KEYWORDS vocabulary in recent session titles / first user
messages, rather than arbitrary frequent words.
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


def _insert_session(db, sid, started_at, *, title=None, source="cli", message_count=1):
    db._conn.execute(
        """
        INSERT INTO sessions (id, source, started_at, title, message_count)
        VALUES (?, ?, ?, ?, ?)
        """,
        (sid, source, started_at, title, message_count),
    )


def _insert_message(db, session_id, role, content, timestamp):
    db._conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        (session_id, role, content, timestamp),
    )


def test_only_security_keywords_returned(overview_db):
    now = time.time()
    _insert_session(overview_db, "s1", now, title="排查 CVE-2026-1234 漏洞")
    _insert_message(overview_db, "s1", "user", "帮我分析这个 phishing 钓鱼攻击", now)

    result = overview_service.list_keywords()
    words = {kw["word"] for kw in result}

    # security terms present
    assert "漏洞" in words
    assert "攻击" in words
    assert "phishing" in words
    assert "cve" in words
    # every returned word is from the curated security vocabulary
    assert words.issubset(set(overview_service._SECURITY_KEYWORDS))


def test_non_security_session_yields_nothing(overview_db):
    now = time.time()
    _insert_session(overview_db, "dev", now, title="写个快速排序函数")
    _insert_message(overview_db, "dev", "user", "用 python 实现一个排序算法就好", now)

    assert overview_service.list_keywords() == []


def test_short_stem_does_not_match_unrelated_word(overview_db):
    # "apt" must not be pulled out of "laptop".
    now = time.time()
    _insert_session(overview_db, "lap", now, title="我的 laptop 坏了帮我看看")
    _insert_message(overview_db, "lap", "user", "laptop 无法开机", now)

    words = {kw["word"] for kw in overview_service.list_keywords()}
    assert "apt" not in words


def test_lang_tag_and_counts(overview_db):
    now = time.time()
    _insert_session(overview_db, "s2", now, title="漏洞 漏洞 malware")
    _insert_message(overview_db, "s2", "user", "又一个 malware 恶意样本", now)

    by_word = {kw["word"]: kw for kw in overview_service.list_keywords()}
    assert by_word["漏洞"]["lang"] == "zh"
    assert by_word["malware"]["lang"] == "en"
    # "漏洞" appears twice in title
    assert by_word["漏洞"]["count"] >= 2
    # "malware" appears in title + first user message
    assert by_word["malware"]["count"] >= 2


def test_old_session_excluded(overview_db):
    old = time.time() - 8 * 86400  # outside the 7-day keyword window
    _insert_session(overview_db, "old", old, title="历史 漏洞 攻击 分析")
    _insert_message(overview_db, "old", "user", "老会话 malware", old)

    assert overview_service.list_keywords() == []
