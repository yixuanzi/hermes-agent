from __future__ import annotations

from fastapi.testclient import TestClient
from hermes_state import SessionDB

import aisoc.backend.services.user_store as _user_store_module
from aisoc.backend.config import load_aisoc_settings
from aisoc.backend.server import create_app
from aisoc.backend.services import cron_service, overview_service, session_service


BOOTSTRAP_PASSWORD = "test-admin-password-123"


REQUIRED_STATUS_KEYS = {
    "status",
    "model",
    "provider",
    "profile",
    "uptime_seconds",
    "last_activity",
}

REQUIRED_STATS_KEYS = {
    "total_sessions",
    "active_sessions",
    "today_tokens",
    "today_input_tokens",
    "today_output_tokens",
    "cron_jobs_total",
    "cron_jobs_enabled",
    "memory_used_chars",
    "memory_total_chars",
    "memory_percent",
    "source_distribution",
}


def _auth_client(monkeypatch) -> tuple[TestClient, dict[str, str]]:
    client, headers, _uid = _auth_client_with_uid(monkeypatch)
    return client, headers


def _auth_client_with_uid(monkeypatch) -> tuple[TestClient, dict[str, str], str]:
    monkeypatch.setenv("AISOC_BOOTSTRAP_ADMIN_PASSWORD", BOOTSTRAP_PASSWORD)
    monkeypatch.setenv("AISOC_JWT_SECRET", "test-jwt-secret-1234567890-abcdef")
    monkeypatch.setattr(_user_store_module, "_STORE", None)
    settings = load_aisoc_settings()
    app = create_app(settings)
    client = TestClient(app)
    login = client.post(
        "/api/auth/login", json={"username": "admin", "password": BOOTSTRAP_PASSWORD}
    )
    assert login.status_code == 200
    body = login.json()
    token = body["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    return client, headers, body["user"]["uid"]


def test_overview_status_requires_auth(monkeypatch) -> None:
    client, headers = _auth_client(monkeypatch)

    unauth = client.get("/api/overview/status")
    assert unauth.status_code == 401

    auth = client.get("/api/overview/status", headers=headers)
    assert auth.status_code == 200
    assert REQUIRED_STATUS_KEYS.issubset(auth.json().keys())


def test_overview_stats_requires_auth(monkeypatch) -> None:
    client, headers = _auth_client(monkeypatch)

    unauth = client.get("/api/overview/stats")
    assert unauth.status_code == 401

    auth = client.get("/api/overview/stats", headers=headers)
    assert auth.status_code == 200
    assert REQUIRED_STATS_KEYS.issubset(auth.json().keys())


def test_overview_token_trend_days_validation(monkeypatch) -> None:
    client, headers = _auth_client(monkeypatch)

    valid_default = client.get("/api/overview/token-trend", headers=headers)
    assert valid_default.status_code == 200

    valid_7 = client.get("/api/overview/token-trend?days=7", headers=headers)
    assert valid_7.status_code == 200

    valid_30 = client.get("/api/overview/token-trend?days=30", headers=headers)
    assert valid_30.status_code == 200

    valid_07 = client.get("/api/overview/token-trend?days=07", headers=headers)
    assert valid_07.status_code == 200

    invalid = client.get("/api/overview/token-trend?days=14", headers=headers)
    assert invalid.status_code == 422
    assert invalid.json() == {"detail": "days must be 7 or 30"}


def test_overview_token_trend_response_shape(monkeypatch) -> None:
    client, headers = _auth_client(monkeypatch)

    response = client.get("/api/overview/token-trend?days=7", headers=headers)
    assert response.status_code == 200

    rows = response.json()
    assert isinstance(rows, list)
    assert len(rows) == 7
    assert set(rows[0].keys()) == {
        "date",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "sessions",
    }


def test_overview_cronjobs_requires_auth(monkeypatch) -> None:
    client, headers = _auth_client(monkeypatch)

    unauth = client.get("/api/overview/cronjobs")
    assert unauth.status_code == 401

    auth = client.get("/api/overview/cronjobs", headers=headers)
    assert auth.status_code == 200
    assert isinstance(auth.json(), list)


def test_overview_cronjob_history_not_found(monkeypatch) -> None:
    client, headers = _auth_client(monkeypatch)

    response = client.get("/api/cron/jobs/missing/history", headers=headers)
    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found"}


def test_overview_cronjobs_handles_cron_backend_failure(monkeypatch) -> None:
    def _boom(*args, **kwargs):
        raise RuntimeError("cron backend down")

    monkeypatch.setattr(overview_service.cron_service, "list_jobs", _boom)
    client, headers = _auth_client(monkeypatch)

    response = client.get("/api/overview/cronjobs", headers=headers)
    assert response.status_code == 200
    assert response.json() == []


def test_overview_cronjob_history_handles_cron_backend_failure(monkeypatch) -> None:
    def _boom(*args, **kwargs):
        raise RuntimeError("cron backend down")

    monkeypatch.setattr(cron_service, "get_job", _boom)
    client, headers = _auth_client(monkeypatch)

    response = client.get("/api/cron/jobs/any/history", headers=headers)
    assert response.status_code == 200
    assert response.json() == []


def test_overview_session_detail_not_found(monkeypatch) -> None:
    client, headers = _auth_client(monkeypatch)

    response = client.get("/api/sessions/missing/detail", headers=headers)
    assert response.status_code == 404
    assert response.json() == {"detail": "Session not found"}


def test_overview_session_detail_truncates_tool_and_skips_empty_assistant(monkeypatch, tmp_path) -> None:
    client, headers, uid = _auth_client_with_uid(monkeypatch)

    db_path = tmp_path / "state.db"
    db = SessionDB(db_path=db_path)
    try:
        db.create_session("sess_1", "cron", model="gpt-5", user_id=uid)
        db.update_token_counts("sess_1", input_tokens=10, output_tokens=20)
        db.append_message("sess_1", "assistant", content="")
        db.append_message("sess_1", "tool", content="x" * 550, tool_name="terminal")
        db.append_message("sess_1", "assistant", content="final")
    finally:
        db.close()

    monkeypatch.setattr(session_service, "SessionDB", lambda: SessionDB(db_path=db_path))

    response = client.get("/api/sessions/sess_1/detail", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "sess_1"
    assert payload["tokens"] == 30
    assert len(payload["messages"]) == 2
    assert payload["messages"][0]["role"] == "tool"
    assert payload["messages"][0]["content"] == ("x" * 500) + "...[truncated]"
    assert payload["messages"][1]["role"] == "assistant"
    assert payload["messages"][1]["content"] == "final"


def test_overview_security_events_requires_auth_and_shape(monkeypatch) -> None:
    captured: dict[str, int] = {}

    def _fake_events(limit: int):
        captured["limit"] = limit
        return [
            {
                "session_id": "cron_alpha001_run_1",
                "type_label": "漏洞追踪",
                "icon": "shield",
                "time": 1_748_348_000.0,
                "duration": 120,
                "tokens": 150,
                "status": "completed",
                "risk_level": "High",
                "summary": "发现高危漏洞",
                "entities": ["CVE-2026-1234"],
                "verdict": "BLOCK",
            }
        ]

    monkeypatch.setattr(overview_service, "list_security_events", _fake_events)
    client, headers = _auth_client(monkeypatch)

    unauth = client.get("/api/overview/security-events")
    assert unauth.status_code == 401

    auth = client.get("/api/overview/security-events?limit=15", headers=headers)
    assert auth.status_code == 200
    body = auth.json()
    assert isinstance(body, list)
    assert body
    assert {
        "session_id",
        "type_label",
        "icon",
        "time",
        "duration",
        "tokens",
        "status",
        "risk_level",
        "summary",
        "entities",
        "verdict",
    }.issubset(body[0].keys())
    assert captured["limit"] == 15


def test_overview_keywords_requires_auth_and_shape(monkeypatch) -> None:
    monkeypatch.setattr(
        overview_service,
        "list_keywords",
        lambda: [{"word": "apollo", "count": 3, "lang": "en"}],
    )
    client, headers = _auth_client(monkeypatch)

    unauth = client.get("/api/overview/keywords")
    assert unauth.status_code == 401

    auth = client.get("/api/overview/keywords", headers=headers)
    assert auth.status_code == 200
    body = auth.json()
    assert isinstance(body, list)
    assert body
    assert set(body[0].keys()) == {"word", "count", "lang"}


def test_overview_keyword_sessions_requires_auth_and_shape(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def _fake_sessions(keyword: str):
        captured["keyword"] = keyword
        return [
            {
                "session_id": "s_kw_1",
                "title": "Apollo review",
                "source": "cli",
                "started_at": 1_748_348_000.0,
                "messages": 6,
                "tokens": 300,
            }
        ]

    monkeypatch.setattr(overview_service, "list_keyword_sessions", _fake_sessions)
    client, headers = _auth_client(monkeypatch)

    unauth = client.get("/api/overview/keywords/apollo/sessions")
    assert unauth.status_code == 401

    auth = client.get("/api/overview/keywords/apollo/sessions", headers=headers)
    assert auth.status_code == 200
    body = auth.json()
    assert isinstance(body, list)
    assert body
    assert set(body[0].keys()) == {
        "session_id",
        "title",
        "source",
        "started_at",
        "messages",
        "tokens",
    }
    assert captured["keyword"] == "apollo"


def test_overview_cron_token_dist_period_validation(monkeypatch) -> None:
    def _fake_dist(period: str):
        return {
            "period": period,
            "total_cron_tokens": 300,
            "non_cron_tokens": 100,
            "grand_total": 400,
            "cron_percent": 75.0,
            "jobs": [
                {
                    "job_id": "alpha001",
                    "name": "Alpha Job",
                    "runs": 2,
                    "input_tokens": 200,
                    "output_tokens": 100,
                    "io_tokens": 300,
                    "cache_read": 0,
                    "cache_write": 0,
                    "percent_of_cron": 100.0,
                    "percent_of_total": 75.0,
                }
            ],
        }

    monkeypatch.setattr(overview_service, "get_cron_token_distribution", _fake_dist)
    client, headers = _auth_client(monkeypatch)

    default_period = client.get("/api/overview/cron-token-dist", headers=headers)
    assert default_period.status_code == 200
    assert default_period.json()["period"] == "today"

    seven_days = client.get("/api/overview/cron-token-dist?period=7d", headers=headers)
    assert seven_days.status_code == 200
    assert seven_days.json()["period"] == "7d"

    thirty_days = client.get("/api/overview/cron-token-dist?period=30d", headers=headers)
    assert thirty_days.status_code == 200
    assert thirty_days.json()["period"] == "30d"

    invalid = client.get("/api/overview/cron-token-dist?period=14d", headers=headers)
    assert invalid.status_code == 422
