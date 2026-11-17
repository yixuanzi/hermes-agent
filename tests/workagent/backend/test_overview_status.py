"""``GET /api/overview/status`` describes the running server, not the session history.

Uptime used to be "time since the first session ever recorded" and provider/model came from
the most recent session row, so an A2A turn without a billing provider showed "Unavailable".
"""

from __future__ import annotations

from contextlib import closing
import sqlite3
import time

from fastapi import FastAPI
from starlette.testclient import TestClient

from hermes_constants import get_hermes_home
from hermes_state import SessionDB
from workagent.backend.routes.overview import build_overview_router

_MONTH = 30 * 86400


def _status() -> dict:
    app = FastAPI()
    app.include_router(build_overview_router())
    with TestClient(app) as client:
        response = client.get("/api/overview/status")
    assert response.status_code == 200
    return response.json()


def _seed_old_session_without_provider() -> None:
    db = SessionDB()
    try:
        db.create_session("a2a-turn", "workagent-a2a", model="session-model")
    finally:
        db.close()
    # The first session ever is a month old; an A2A turn records no billing provider.
    with closing(sqlite3.connect(get_hermes_home() / "state.db")) as conn, conn:
        conn.execute(
            "UPDATE sessions SET started_at = ?, billing_provider = NULL WHERE id = ?",
            (time.time() - _MONTH, "a2a-turn"),
        )


def test_status_reports_process_uptime_and_configured_model() -> None:
    (get_hermes_home() / "config.yaml").write_text(
        "model:\n  provider: openrouter\n  default: vendor/configured-model\n", encoding="utf-8"
    )
    _seed_old_session_without_provider()

    status = _status()

    assert status["provider"] == "openrouter"
    assert status["model"] == "vendor/configured-model"
    assert 0 <= status["uptime_seconds"] < _MONTH / 2


def test_status_falls_back_to_the_latest_session_without_a_configured_model() -> None:
    _seed_old_session_without_provider()

    assert _status()["model"] == "session-model"
