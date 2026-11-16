"""Cron job ownership (``identify``): storage contract, and runs that act as the owning user."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


@pytest.fixture
def hermes_env(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    (home / "scripts").mkdir(parents=True)
    (home / "cron").mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    import importlib
    import hermes_constants
    importlib.reload(hermes_constants)
    import cron.jobs
    importlib.reload(cron.jobs)
    import cron.scheduler
    importlib.reload(cron.scheduler)
    (home / "users.env.json").write_text(
        json.dumps({"slack.u123": {"CURRENT_USER_NAME": "alice", "CUSTOM_TOKEN": "abc123"}}), encoding="utf-8")
    return home


ALICE = {"platform": "slack", "user_id": "u123", "user_name": "alice"}


def test_identify_is_built_only_for_a_real_user_and_parsed_strictly():
    from cron.jobs_identify import build_job_identify, parse_job_identify

    assert build_job_identify("slack", "", "x") is None
    assert build_job_identify(" slack ", " u123 ", " alice ") == ALICE
    assert parse_job_identify(None) is None
    for bad in ("u123", {"platform": "slack", "user_id": " "}, {"platform": 1, "user_id": "u", "user_name": ""}):
        with pytest.raises(ValueError):
            parse_job_identify(bad)


def test_public_jobs_are_visible_to_all_owned_jobs_to_their_owner_and_malformed_to_none():
    from cron.jobs_identify import is_job_visible_to_identity

    alice = SimpleNamespace(platform="slack", user_id="u123")
    bob = SimpleNamespace(platform="slack", user_id="u456")
    assert is_job_visible_to_identity({}, None) and is_job_visible_to_identity({}, bob)
    owned = {"identify": ALICE}
    assert is_job_visible_to_identity(owned, alice)
    assert not is_job_visible_to_identity(owned, bob)
    assert not is_job_visible_to_identity(owned, None)
    assert not is_job_visible_to_identity({"identify": "alice"}, alice)


def test_create_persists_an_owner_only_when_given(hermes_env):
    from cron.jobs import create_job, get_job, load_jobs

    public = create_job(prompt="public", schedule="every 1h")
    owned = create_job(prompt="owned", schedule="every 1h", identify=dict(ALICE))

    stored = {job["id"]: job for job in load_jobs()}
    assert "identify" not in stored[public["id"]]
    assert stored[owned["id"]]["identify"] == ALICE
    assert "identify" not in get_job(public["id"])
    with pytest.raises(ValueError):
        create_job(prompt="bad", schedule="every 1h", identify={"user_id": "u1"})


def test_owner_can_be_corrected_but_routing_metadata_never_changes(hermes_env):
    from cron.jobs import create_job, get_job, update_job

    job = create_job(prompt="reassign", schedule="every 1h", identify=dict(ALICE))
    bob = {"platform": "slack", "user_id": "u456", "user_name": "bob"}

    assert update_job(job["id"], {"identify": bob})["identify"] == bob
    assert get_job(job["id"])["identify"] == bob
    with pytest.raises(ValueError):
        update_job(job["id"], {"identify": {"platform": "slack"}})
    for field in ("profile", "profile_name"):
        with pytest.raises(ValueError, match="cannot be updated"):
            update_job(job["id"], {field: "worker_alpha"})


def test_a_hand_edited_owner_is_flagged_not_treated_as_public(hermes_env):
    from cron.jobs import create_job, get_job, load_jobs, save_jobs

    job = create_job(prompt="owned", schedule="every 1h", identify=dict(ALICE))
    jobs = load_jobs()
    jobs[0]["identify"] = "alice"
    save_jobs(jobs)

    assert get_job(job["id"])["_identify_error"] == "malformed identify"


def test_a_no_agent_job_runs_its_script_with_the_owners_userenv(hermes_env):
    from cron.jobs import create_job
    from cron.scheduler import run_job
    from tools.user_env_runtime import get_current_user_env_identity

    (hermes_env / "scripts" / "env.sh").write_text('#!/bin/bash\necho "${CUSTOM_TOKEN:-missing}"\n')
    job = create_job(prompt=None, schedule="every 5m", script="env.sh", no_agent=True, deliver="local",
                     identify=dict(ALICE))

    success, _doc, final_response, error = run_job(job)

    assert (success, error, final_response.strip()) == (True, None, "abc123")
    assert get_current_user_env_identity() is None  # the owner never outlives the run


def test_a_public_job_script_does_not_see_any_users_env(hermes_env):
    from cron.jobs import create_job
    from cron.scheduler import run_job

    (hermes_env / "scripts" / "env.sh").write_text('#!/bin/bash\necho "${CUSTOM_TOKEN:-missing}"\n')
    job = create_job(prompt=None, schedule="every 5m", script="env.sh", no_agent=True, deliver="local")

    assert run_job(job)[2].strip() == "missing"


def test_a_malformed_owner_fails_the_run_instead_of_running_as_public(hermes_env):
    from cron.scheduler import run_job

    with pytest.raises(RuntimeError, match="malformed identify"):
        run_job({"id": "j1", "no_agent": True, "script": "env.sh", "identify": {"user_id": 7}})


def test_the_prerun_script_sees_the_owners_userenv(hermes_env):
    from cron.scheduler import _build_job_prompt

    script = hermes_env / "scripts" / "data.py"
    script.write_text("import os\nprint('token=' + os.environ.get('CUSTOM_TOKEN', 'missing'))\n")

    prompt = _build_job_prompt({"prompt": "Report.", "script": str(script), "identify": dict(ALICE)})

    assert "token=missing" not in prompt
    assert "token=" in prompt


def test_the_monitor_script_runs_as_the_owner_and_a_bad_owner_fails_only_the_monitor(hermes_env):
    from cron.monitor import _run_monitor_source

    (hermes_env / "scripts" / "watch.sh").write_text('#!/bin/bash\necho "${CUSTOM_TOKEN:-missing}"\n')

    ok, output = _run_monitor_source({"id": "m1", "monitor_script": "watch.sh", "identify": dict(ALICE)})
    assert (ok, output.strip()) == (True, "abc123")
    ok, output = _run_monitor_source({"id": "m2", "monitor_script": "watch.sh", "identify": {"user_id": 7}})
    assert ok is False and "malformed identify" in output


def test_the_cron_agent_acts_for_the_owner_but_stays_a_cron_agent():
    from cron.jobs_identify import as_job_owner

    agent = as_job_owner({"identify": dict(ALICE)}, SimpleNamespace(platform="cron"))
    assert (agent.platform, agent._user_env_platform, agent._user_id, agent._user_name) == (
        "cron", "slack", "u123", "alice")
    public = as_job_owner({}, SimpleNamespace(platform="cron"))
    assert not hasattr(public, "_user_id")
