"""Cron routes against the real ``cron.jobs`` storage (only the profile-dir lookup is pinned).

The route tests elsewhere stub ``cron_service``, which is how a stale ``profile=`` kwarg to
``cron.jobs.create_job`` shipped: every create from the console answered 500.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from workagent.backend.routes.cron import build_cron_router
from workagent.backend.services import cron_service


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


def test_update_accepts_echoed_detail_annotations(client: TestClient) -> None:
    job_id = client.post("/api/cron/jobs", json={"prompt": "x", "schedule": "every 2h"}).json()["id"]
    detail = client.get(f"/api/cron/jobs/{job_id}").json()
    echoed = {key: detail[key] for key in ("profile", "profile_name", "hermes_home", "is_default_profile")}

    updated = client.put(f"/api/cron/jobs/{job_id}", json={"updates": {**echoed, "prompt": "y"}})

    assert updated.status_code == 200, updated.text
    assert updated.json()["prompt"] == "y"


def test_history_is_empty_before_the_profile_has_a_session_store(client: TestClient, tmp_path: Path) -> None:
    job_id = client.post("/api/cron/jobs", json={"prompt": "x", "schedule": "every 2h"}).json()["id"]

    assert not (tmp_path / "state.db").exists()
    assert client.get(f"/api/cron/jobs/{job_id}/history").json() == []


@pytest.mark.asyncio
async def test_history_lists_unified_runtime_runs(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scheduler fire is a ``local-*`` session bound to its job only by its admission."""
    from tests.fakes.cron_owner import admit_fire, cron_owner

    owner = cron_owner(tmp_path, monkeypatch)
    try:
        job_id = client.post("/api/cron/jobs", json={"prompt": "x", "schedule": "every 2h"}).json()["id"]
        session_id, _execution_id = await admit_fire(owner, job_id)
    finally:
        owner.db.close()

    history = client.get(f"/api/cron/jobs/{job_id}/history").json()

    assert session_id.startswith("local-")
    assert [row["session_id"] for row in history] == [session_id]
    assert history[0]["messages"] == 1

    from workagent.backend.services import overview_service

    monkeypatch.setattr(cron_service, "_cron_profile_dicts", lambda: [{"name": "default"}])
    [panel_row] = overview_service.get_cronjobs()
    assert panel_row["last_run"]["session_id"] == session_id


def test_update_rejects_profile_move(client: TestClient) -> None:
    job_id = client.post("/api/cron/jobs", json={"prompt": "x", "schedule": "every 2h"}).json()["id"]

    moved = client.put(f"/api/cron/jobs/{job_id}", json={"updates": {"profile": "other", "prompt": "y"}})

    assert moved.status_code == 400
    assert client.get(f"/api/cron/jobs/{job_id}").json()["prompt"] == "x"
