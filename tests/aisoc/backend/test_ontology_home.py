"""Ontology artifacts belong to the profile serving the backend.

Every default ontology path derives from the running ``HERMES_HOME``, reads
never create directories, and the AI judge subprocess inherits that home
instead of being pointed at a hardcoded ``aisoc`` profile.
"""

from __future__ import annotations

import importlib
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.waiting import wait_until


BOOTSTRAP_PASSWORD = "test-admin-password-123"
_PATH_OVERRIDES = (
    "AISOC_ONTOLOGY_STANDARD_GRAPH_V3",
    "AISOC_ONTOLOGY_STANDARD_GRAPH",
    "AISOC_ONTOLOGY_SCANS_ROOT",
    "AISOC_ONTOLOGY_SKILL_SCRIPTS",
    "AISOC_ONTOLOGY_JOBS_ROOT",
)


@pytest.fixture
def profile_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "profile-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("AISOC_JWT_SECRET", "test-jwt-secret-1234567890-abcdef")
    monkeypatch.setenv("AISOC_BOOTSTRAP_ADMIN_PASSWORD", BOOTSTRAP_PASSWORD)
    for name in _PATH_OVERRIDES:
        monkeypatch.delenv(name, raising=False)
    return home


@pytest.fixture
def ontology_client(load_backend, profile_home: Path):
    server = load_backend("aisoc.backend.server")
    with TestClient(server.create_app()) as client:
        token = client.post(
            "/api/auth/login", json={"username": "admin", "password": BOOTSTRAP_PASSWORD}
        ).json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        yield client


def _created_outside(home: Path, root: Path, before: set[Path]) -> list[Path]:
    return [p for p in set(root.rglob("*")) - before if home not in p.parents]


def test_unknown_scan_job_read_creates_no_directories(ontology_client, profile_home, tmp_path):
    before = set(tmp_path.rglob("*"))

    response = ontology_client.get("/api/ontology/scan/job-0000000000")

    assert response.status_code == 404
    assert set(tmp_path.rglob("*")) == before


def test_scan_job_and_judge_subprocess_use_running_profile_home(
    ontology_client, profile_home, tmp_path, monkeypatch
):
    ai = importlib.import_module("aisoc.backend.services.ontology_ai_service")
    scans = profile_home / "artifacts" / "ontology" / "scans" / "scan-ai-1"
    seen: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(cmd, **kwargs):
        seen.append((cmd, kwargs["env"]))
        return subprocess.CompletedProcess(cmd, 0, stdout='{"judgments": []}', stderr="")

    def fake_collect():
        bundle_dir = scans.parent / "collect-1"
        bundle_dir.mkdir(parents=True)
        (bundle_dir / "evidence-bundle.json").write_text(
            '{"objects": [{"object_id": "o1", "candidate_evidence": []}]}', encoding="utf-8"
        )
        return {"output_dir": str(bundle_dir), "scan_id": "collect-1"}

    def fake_score(judgments_path, promote_latest=True):
        scans.mkdir(parents=True)
        return {"scan_id": "scan-ai-1", "output_dir": str(scans), "completeness_score": 1.0}

    monkeypatch.setattr(ai.subprocess, "run", fake_run)
    monkeypatch.setattr(ai.OntologyAIService, "collect", staticmethod(fake_collect))
    monkeypatch.setattr(ai.OntologyAIService, "score", staticmethod(fake_score))

    before = set(tmp_path.rglob("*"))
    job = ontology_client.post("/api/ontology/scan").json()
    job_url = f"/api/ontology/scan/{job['job_id']}"
    wait_until(lambda: ontology_client.get(job_url).json()["status"] in ("completed", "failed"))

    assert ontology_client.get(job_url).json()["status"] == "completed"
    assert (profile_home / "artifacts" / "ontology" / "jobs" / f"{job['job_id']}.json").is_file()
    assert _created_outside(profile_home, tmp_path, before) == []
    (cmd, env), = seen
    assert "--profile" not in cmd
    assert env["HERMES_HOME"] == str(profile_home)
