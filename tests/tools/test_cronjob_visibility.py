"""The ``cronjob`` tool shows and operates cron jobs only for the calling platform user."""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def cron_home(tmp_path, monkeypatch):
    monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")
    return tmp_path


def _as(user_id, call, *args, **kwargs):
    from tools.user_env_runtime import reset_current_user_env_identity, set_current_user_env_identity

    token = set_current_user_env_identity("slack", user_id, user_id)
    try:
        return call(*args, **kwargs)
    finally:
        reset_current_user_env_identity(token)


def _job(name, identify=None):
    from cron.jobs import create_job
    return create_job(prompt=f"run {name}", schedule="every 1h", name=name, identify=identify)


def _owner(user_id):
    return {"platform": "slack", "user_id": user_id, "user_name": user_id}


def _call(user_id, **kwargs):
    from tools.cronjob_tools import cronjob
    return json.loads(_as(user_id, cronjob, **kwargs))


def test_a_user_lists_their_own_and_public_jobs_only(cron_home):
    mine, theirs, public = _job("mine", _owner("alice")), _job("theirs", _owner("bob")), _job("public")

    listed = {job["job_id"] for job in _call("alice", action="list")["jobs"]}

    assert listed == {mine["id"], public["id"]}
    assert theirs["id"] not in listed


def test_another_users_job_reads_as_not_found_for_every_action(cron_home):
    theirs = _job("theirs", _owner("bob"))

    for action in ("pause", "resume", "remove"):
        result = _call("alice", action=action, job_id=theirs["id"])
        assert result["success"] is False and "not found" in result["error"]
    assert _call("bob", action="pause", job_id=theirs["id"])["success"] is True


def test_a_shared_name_resolves_among_the_callers_jobs_and_never_leaks_others(cron_home):
    mine = _job("daily", _owner("alice"))
    _job("daily", _owner("bob"))

    result = _call("alice", action="pause", job_id="daily")

    assert result["success"] is True and result["job"]["job_id"] == mine["id"]


def test_context_from_cannot_reach_another_users_output(cron_home):
    from tools.cronjob_job_args import _validate_context_from_refs

    theirs = _job("theirs", _owner("bob"))

    assert "not found" in _as("alice", _validate_context_from_refs, [theirs["id"]])
    assert _as("bob", _validate_context_from_refs, [theirs["id"]]) is None
