"""Session read endpoints never return the stored system prompt.

The list endpoint already stripped it as sensitive, but ``GET /api/sessions/{id}`` returned the
whole ``system_prompt`` (skills, memory, user profile) to any console caller.
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.testclient import TestClient

from hermes_state import SessionDB
from workagent.backend.routes.sessions import build_sessions_router

_PROMPT = "You are Hermes. MEMORY: the user's private notes live in ~/vault."


def test_single_session_and_detail_omit_system_prompt() -> None:
    db = SessionDB()
    try:
        db.create_session("s-1", "cli", model="test-model", system_prompt=_PROMPT)
        db.append_message("s-1", "user", "hello")
        db.append_message("s-1", "assistant", "hi there")
    finally:
        db.close()

    app = FastAPI()
    app.include_router(build_sessions_router())
    with TestClient(app) as client:
        summary = client.get("/api/sessions/s-1")
        detail = client.get("/api/sessions/s-1/detail")

    assert summary.status_code == 200 and detail.status_code == 200
    assert summary.json()["id"] == "s-1"
    assert summary.json()["model"] == "test-model"
    assert detail.json()["message_count"] == 2
    for response in (summary, detail):
        assert "system_prompt" not in response.json()
        assert "private notes" not in response.text
