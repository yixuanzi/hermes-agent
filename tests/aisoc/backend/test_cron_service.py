from __future__ import annotations

import pytest
from fastapi import HTTPException

from aisoc.backend.services import cron_service


def test_update_job_raw_rejects_id_change(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cron_service,
        "get_job",
        lambda job_id, profile=None: {"id": "job-1", "name": "before", "profile": "default"},
    )

    with pytest.raises(HTTPException) as exc:
        cron_service.update_job_raw("job-1", {"id": "other-job", "name": "after"}, profile="default")

    assert exc.value.status_code == 400
    assert "id" in str(exc.value.detail)


def test_update_job_raw_rejects_profile_change(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cron_service,
        "get_job",
        lambda job_id, profile=None: {"id": "job-1", "name": "before", "profile": "default"},
    )

    with pytest.raises(HTTPException) as exc:
        cron_service.update_job_raw("job-1", {"name": "after", "profile": "worker_alpha"}, profile="default")

    assert exc.value.status_code == 400
    assert "profile" in str(exc.value.detail)


def test_update_job_raw_accepts_identify_object_and_filters_annotations(monkeypatch: pytest.MonkeyPatch) -> None:
    current_job = {
        "id": "job-1",
        "name": "before",
        "prompt": "before prompt",
        "profile": "default",
        "identify": None,
        "profile_name": "default",
        "hermes_home": "/tmp/default",
        "is_default_profile": True,
    }
    captured: dict[str, object] = {}

    monkeypatch.setattr(cron_service, "get_job", lambda job_id, profile=None: dict(current_job))

    def _update(job_id: str, updates: dict, profile: str | None = None):
        captured["job_id"] = job_id
        captured["updates"] = updates
        captured["profile"] = profile
        return {"id": job_id, **updates, "profile": profile}

    monkeypatch.setattr(cron_service, "update_job", _update)

    result = cron_service.update_job_raw(
        "job-1",
        {
            "id": "job-1",
            "name": "after",
            "prompt": "after prompt",
            "profile": "default",
            "identify": {"platform": "slack", "user_id": "u-1", "user_name": "alice"},
            "profile_name": "worker_alpha",
            "hermes_home": "/tmp/other",
            "is_default_profile": False,
        },
        profile="default",
    )

    assert captured == {
        "job_id": "job-1",
        "updates": {
            "name": "after",
            "prompt": "after prompt",
            "identify": {"platform": "slack", "user_id": "u-1", "user_name": "alice"},
        },
        "profile": "default",
    }
    assert result == {
        "id": "job-1",
        "name": "after",
        "prompt": "after prompt",
        "identify": {"platform": "slack", "user_id": "u-1", "user_name": "alice"},
        "profile": "default",
    }


def test_update_job_raw_allows_null_identify(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cron_service,
        "get_job",
        lambda job_id, profile=None: {
            "id": "job-1",
            "name": "before",
            "profile": "default",
            "identify": {"platform": "slack", "user_id": "u-1", "user_name": "alice"},
        },
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        cron_service,
        "update_job",
        lambda job_id, updates, profile=None: captured.update(
            {"job_id": job_id, "updates": updates, "profile": profile},
        ) or {"id": job_id, **updates, "profile": profile},
    )

    cron_service.update_job_raw("job-1", {"identify": None, "profile": "default"}, profile="default")

    assert captured["updates"] == {"identify": None}


def test_update_job_raw_rejects_invalid_identify(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cron_service,
        "get_job",
        lambda job_id, profile=None: {"id": "job-1", "name": "before", "profile": "default"},
    )

    with pytest.raises(HTTPException) as exc:
        cron_service.update_job_raw(
            "job-1",
            {"identify": {"platform": "slack", "user_name": "alice"}, "profile": "default"},
            profile="default",
        )

    assert exc.value.status_code == 400
    assert "identify" in str(exc.value.detail)
