"""SkillHub install leaves config alone when it fails, and asking to sync a
skill that is not installed is a 404, not a hub failure."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def os_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "os-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def _external_dirs() -> list:
    from hermes_cli.config import load_config

    return list((load_config().get("skills") or {}).get("external_dirs") or [])


def test_failed_install_does_not_register_the_skills_dir(test_client, auth_headers):
    before = _external_dirs()

    resp = test_client.post("/api/skill-hub/skills", headers=auth_headers, json={"identifier": "ghost"})

    assert resp.status_code == 400
    assert _external_dirs() == before


def test_syncing_a_skill_that_is_not_installed_is_not_found(test_client, auth_headers):
    resp = test_client.post("/api/skill-hub/skills/ghost/sync", headers=auth_headers)

    assert resp.status_code == 404
