from __future__ import annotations

from aisoc.backend.services import cron_service


def test_list_cron_jobs_returns_profile_annotations(test_client, auth_headers, monkeypatch) -> None:
    monkeypatch.setattr(
        cron_service,
        "list_jobs",
        lambda profile="default": [
            {"id": "job-1", "profile": "default", "profile_name": "default"}
        ],
    )
    resp = test_client.get("/api/cron/jobs", headers=auth_headers)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["items"][0]["profile"] == "default"
    assert payload["page_size"] == 12
    assert payload["total"] == 1


def test_list_cron_jobs_supports_pagination_query(test_client, auth_headers, monkeypatch) -> None:
    monkeypatch.setattr(
        cron_service,
        "list_jobs",
        lambda profile="default": [
            {"id": f"job-{idx}", "profile": "default", "profile_name": "default"}
            for idx in range(1, 26)
        ],
    )

    resp = test_client.get("/api/cron/jobs?page=2&page_size=12", headers=auth_headers)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["page"] == 2
    assert payload["page_size"] == 12
    assert payload["total"] == 25
    assert payload["total_pages"] == 3
    assert payload["has_prev"] is True
    assert payload["has_next"] is True
    assert len(payload["items"]) == 12
    assert payload["items"][0]["id"] == "job-13"


def test_cron_routes_default_profile_to_runtime_profile(test_client, auth_headers, monkeypatch) -> None:
    monkeypatch.setattr(cron_service, "get_runtime_profile_name", lambda: "worker_alpha")

    observed: dict[str, str] = {}

    def _capture(name: str, profile: str | None) -> None:
        observed[name] = str(profile)

    monkeypatch.setattr(
        cron_service,
        "list_jobs",
        lambda profile=None: (
            _capture("list_jobs", profile),
            [{"id": "job-1", "name": "daily", "profile": profile}],
        )[1],
    )
    monkeypatch.setattr(
        cron_service,
        "get_job",
        lambda job_id, profile=None: (
            _capture("get_job", profile),
            {"id": job_id, "name": "daily", "profile": profile},
        )[1],
    )
    monkeypatch.setattr(
        cron_service,
        "get_job_history",
        lambda job_id, profile=None: (
            _capture("get_job_history", profile),
            [{"session_id": f"cron_{job_id}_run_1"}],
        )[1],
    )
    monkeypatch.setattr(
        cron_service,
        "create_job",
        lambda profile, **kwargs: (
            _capture("create_job", profile),
            {"id": "job-new", "profile": profile, **kwargs},
        )[1],
    )
    monkeypatch.setattr(
        cron_service,
        "update_job",
        lambda job_id, updates, profile=None: (
            _capture("update_job", profile),
            {"id": job_id, "profile": profile, **updates},
        )[1],
    )
    monkeypatch.setattr(
        cron_service,
        "pause_job",
        lambda job_id, profile=None: (
            _capture("pause_job", profile),
            {"id": job_id, "profile": profile, "state": "paused"},
        )[1],
    )
    monkeypatch.setattr(
        cron_service,
        "resume_job",
        lambda job_id, profile=None: (
            _capture("resume_job", profile),
            {"id": job_id, "profile": profile, "state": "running"},
        )[1],
    )
    monkeypatch.setattr(
        cron_service,
        "trigger_job",
        lambda job_id, profile=None: (
            _capture("trigger_job", profile),
            {"id": job_id, "profile": profile, "state": "triggered"},
        )[1],
    )
    monkeypatch.setattr(
        cron_service,
        "remove_job",
        lambda job_id, profile=None: (
            _capture("remove_job", profile),
            True,
        )[1],
    )

    headers = auth_headers
    assert test_client.get("/api/cron/jobs", headers=headers).status_code == 200
    assert test_client.get("/api/cron/jobs/job-1", headers=headers).status_code == 200
    assert test_client.get("/api/cron/jobs/job-1/history", headers=headers).status_code == 200
    assert (
        test_client.post(
            "/api/cron/jobs",
            headers=headers,
            json={
                "name": "daily-test-msg",
                "prompt": "test",
                "schedule": "* * * * *",
                "deliver": "slack",
                "skills": [],
                "skill": None,
                "enabled_toolsets": None,
                "model": "deepseek-v4-flash",
                "provider": None,
                "base_url": None,
                "script": None,
                "workdir": None,
                "no_agent": False,
            },
        ).status_code
        == 200
    )
    assert (
        test_client.put(
            "/api/cron/jobs/job-1",
            headers=headers,
            json={"updates": {"deliver": "slack"}},
        ).status_code
        == 200
    )
    assert test_client.post("/api/cron/jobs/job-1/pause", headers=headers).status_code == 200
    assert test_client.post("/api/cron/jobs/job-1/resume", headers=headers).status_code == 200
    assert test_client.post("/api/cron/jobs/job-1/trigger", headers=headers).status_code == 200
    assert test_client.delete("/api/cron/jobs/job-1", headers=headers).status_code == 200

    assert observed == {
        "list_jobs": "worker_alpha",
        "get_job": "worker_alpha",
        "get_job_history": "worker_alpha",
        "create_job": "worker_alpha",
        "update_job": "worker_alpha",
        "pause_job": "worker_alpha",
        "resume_job": "worker_alpha",
        "trigger_job": "worker_alpha",
        "remove_job": "worker_alpha",
    }


def test_create_cron_job_accepts_extended_payload_fields(test_client, auth_headers, monkeypatch) -> None:
    monkeypatch.setattr(cron_service, "get_runtime_profile_name", lambda: "default")

    captured: dict[str, object] = {}

    def _create(profile: str, **kwargs):
        captured["profile"] = profile
        captured.update(kwargs)
        return {"id": "job-1", "profile": profile, **kwargs}

    monkeypatch.setattr(cron_service, "create_job", _create)

    payload = {
        "name": "daily-test-msg",
        "prompt": "向当前对话发送一条测试消息，内容为：这是一条每日测试消息。",
        "schedule": "* * * * *",
        "deliver": "slack",
        "skills": [],
        "skill": None,
        "enabled_toolsets": None,
        "model": "deepseek-v4-flash",
        "provider": None,
        "base_url": None,
        "script": None,
        "workdir": None,
        "no_agent": False,
    }
    response = test_client.post("/api/cron/jobs", headers=auth_headers, json=payload)

    assert response.status_code == 200
    assert captured == {"profile": "default", **payload}


def test_raw_cron_job_update_route_uses_runtime_profile(test_client, auth_headers, monkeypatch) -> None:
    monkeypatch.setattr(cron_service, "get_runtime_profile_name", lambda: "worker_alpha")

    observed: dict[str, object] = {}

    def _update_raw(job_id: str, job: dict, profile: str | None = None):
        observed["job_id"] = job_id
        observed["job"] = job
        observed["profile"] = profile
        return {"id": job_id, **job, "profile": profile}

    monkeypatch.setattr(cron_service, "update_job_raw", _update_raw)

    response = test_client.put(
        "/api/cron/jobs/job-1/raw",
        headers=auth_headers,
        json={"job": {"name": "renamed", "identify": None}},
    )

    assert response.status_code == 200
    assert observed == {
        "job_id": "job-1",
        "job": {"name": "renamed", "identify": None},
        "profile": "worker_alpha",
    }


def test_raw_cron_job_update_route_returns_not_found(test_client, auth_headers, monkeypatch) -> None:
    monkeypatch.setattr(cron_service, "get_runtime_profile_name", lambda: "default")
    monkeypatch.setattr(cron_service, "update_job_raw", lambda job_id, job, profile=None: None)

    response = test_client.put(
        "/api/cron/jobs/missing/raw",
        headers=auth_headers,
        json={"job": {"name": "renamed"}},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found"}
