"""Tests for aisoc.backend overview_service model-usage + cost aggregation.

Exercises get_model_usage() (per-model token/cost breakdown over a period) and
the today_cost_usd figure added to get_stats(). Cost is summed from the stored
sessions.actual_cost_usd / estimated_cost_usd columns (prefer actual, fall back
to estimated, then 0) — there is no query-time pricing table.
"""

import time

import pytest

import hermes_state
from hermes_state import SessionDB
from aisoc.backend.services import overview_service


@pytest.fixture()
def overview_db(tmp_path, monkeypatch):
    """Isolated SessionDB; also redirect the service's default DB to it."""
    db_path = tmp_path / "state.db"
    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", db_path)
    db = SessionDB(db_path=db_path)
    yield db
    db.close()


def _insert(
    db,
    sid,
    model,
    started_at,
    input_tokens,
    output_tokens,
    *,
    actual=None,
    estimated=None,
    cache_read=0,
    cache_write=0,
    reasoning=0,
    source="cli",
):
    db._conn.execute(
        """
        INSERT INTO sessions
            (id, source, model, started_at, input_tokens, output_tokens,
             cache_read_tokens, cache_write_tokens, reasoning_tokens,
             actual_cost_usd, estimated_cost_usd)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sid,
            source,
            model,
            started_at,
            input_tokens,
            output_tokens,
            cache_read,
            cache_write,
            reasoning,
            actual,
            estimated,
        ),
    )


def _seed(db):
    """Three sessions today (2 opus, 1 sonnet) + one opus 8 days ago."""
    now = time.time()
    old = now - 8 * 86400
    # A: actual present -> uses actual (0.25), also carries cache/reasoning
    _insert(db, "a", "claude-opus-4-8", now, 1000, 500, actual=0.25, estimated=0.30, cache_read=200, reasoning=50)
    # B: actual NULL -> falls back to estimated (0.40)
    _insert(db, "b", "claude-opus-4-8", now, 2000, 1000, actual=None, estimated=0.40)
    # C: sonnet, estimated NULL -> uses actual (0.05)
    _insert(db, "c", "claude-sonnet-4-6", now, 500, 100, actual=0.05, estimated=None)
    # D: opus, 8 days ago -> excluded from today/7d, included in 30d
    _insert(db, "d", "claude-opus-4-8", old, 9999, 9999, actual=9.99)
    return now


def test_model_usage_today(overview_db):
    _seed(overview_db)

    result = overview_service.get_model_usage("today")

    assert result["period"] == "today"
    assert result["total_tokens"] == 5100
    assert result["total_cost_usd"] == pytest.approx(0.70)

    models = result["models"]
    assert len(models) == 2
    # Sorted by (input+output) desc -> opus first.
    opus, sonnet = models[0], models[1]

    assert opus["model"] == "claude-opus-4-8"
    assert opus["sessions"] == 2
    assert opus["input_tokens"] == 3000
    assert opus["output_tokens"] == 1500
    assert opus["total_tokens"] == 4500
    assert opus["cost_usd"] == pytest.approx(0.65)
    assert opus["percent_of_total"] == pytest.approx(88.2)
    # cache/reasoning carried through for the legend
    assert opus["cache_read_tokens"] == 200
    assert opus["reasoning_tokens"] == 50

    assert sonnet["model"] == "claude-sonnet-4-6"
    assert sonnet["total_tokens"] == 600
    assert sonnet["cost_usd"] == pytest.approx(0.05)
    assert sonnet["percent_of_total"] == pytest.approx(11.8)

    # percentages sum to ~100
    assert sum(m["percent_of_total"] for m in models) == pytest.approx(100.0)


def test_model_usage_30d_includes_older_sessions(overview_db):
    _seed(overview_db)

    today = overview_service.get_model_usage("today")
    month = overview_service.get_model_usage("30d")

    # The 8-day-old opus session is excluded from today but present in 30d.
    assert month["total_tokens"] == today["total_tokens"] + 19998
    opus = next(m for m in month["models"] if m["model"] == "claude-opus-4-8")
    assert opus["total_tokens"] == 4500 + 19998
    assert opus["cost_usd"] == pytest.approx(10.64)


def test_model_usage_empty_period(overview_db):
    result = overview_service.get_model_usage("today")
    assert result["models"] == []
    assert result["total_tokens"] == 0
    assert result["total_cost_usd"] == 0.0


def test_model_usage_invalid_period(overview_db):
    with pytest.raises(ValueError):
        overview_service.get_model_usage("year")


def test_stats_includes_today_cost(overview_db):
    _seed(overview_db)

    stats = overview_service.get_stats()
    assert "today_cost_usd" in stats
    # today rows only: 0.25 + 0.40 + 0.05
    assert stats["today_cost_usd"] == pytest.approx(0.70)


def test_stats_memory_breakdown(overview_db, monkeypatch):
    monkeypatch.setattr(overview_service.memory_service, "read_soul", lambda: {"content": "s" * 550})
    monkeypatch.setattr(
        overview_service.memory_service, "read_user_preferences", lambda: {"content": "u" * 275}
    )

    stats = overview_service.get_stats()

    # soul: 550 / 2200 default limit = 25%; user: 275 / 1375 = 20%
    assert stats["memory_soul_chars"] == 550
    assert stats["memory_soul_limit"] == 2200
    assert stats["memory_soul_percent"] == pytest.approx(25.0)
    assert stats["memory_user_chars"] == 275
    assert stats["memory_user_limit"] == 1375
    assert stats["memory_user_percent"] == pytest.approx(20.0)
    # combined still consistent
    assert stats["memory_used_chars"] == 825
    assert stats["memory_total_chars"] == 3575
    assert stats["memory_percent"] == pytest.approx(round(825 / 3575 * 100, 1))
