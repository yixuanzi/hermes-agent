"""Cron routes against the real ``cron.jobs`` storage (only the profile-dir lookup is pinned).

The route tests elsewhere stub ``cron_service``, which is how a stale ``profile=`` kwarg to
``cron.jobs.create_job`` shipped: every create from the console answered 500.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from aisoc.backend.routes.cron import build_cron_router
from aisoc.backend.services import cron_service


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setattr(cron_service, "get_runtime_profile_name", lambda: "default")
    monkeypatch.setattr(cron_service, "_cron_profile_home", lambda profile: ("default", tmp_path))
    app = FastAPI()
    app.include_router(build_cron_router())
    return TestClient(app)


def test_create_job_persists_and_round_trips(client: TestClient, tmp_path: Path) -> None:
    created = client.post("/api/cron/jobs", json={"name": "digest", "prompt": "say hi", "schedule": "every 2h"})

    assert created.status_code == 200, created.text
    job_id = created.json()["id"]
    assert job_id in (tmp_path / "cron" / "jobs.json").read_text(encoding="utf-8")
    assert client.get(f"/api/cron/jobs/{job_id}").json()["name"] == "digest"
    assert client.delete(f"/api/cron/jobs/{job_id}").status_code == 200


def test_invalid_cron_input_is_a_client_error(client: TestClient) -> None:
    assert client.post("/api/cron/jobs", json={"prompt": "x", "schedule": "not a schedule"}).status_code == 400

    job_id = client.post("/api/cron/jobs", json={"prompt": "x", "schedule": "every 2h"}).json()["id"]
    bad_schedule = client.put(f"/api/cron/jobs/{job_id}", json={"updates": {"schedule": "not a schedule"}})
    id_change = client.put(f"/api/cron/jobs/{job_id}", json={"updates": {"id": "other"}})

    assert bad_schedule.status_code == 400
    assert id_change.status_code == 400
