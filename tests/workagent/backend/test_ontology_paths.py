"""The ontology console keeps every file inside the profile the server runs as.

Its defaults used to be pinned to ``<passwd home>/.hermes/profiles/workagent`` and the AI
judgment ran ``hermes --profile workagent``, so a scan from any profile wrote into the real
home's ``workagent`` profile.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from tests.waiting import wait_until
from workagent.backend.routes.ontology import build_ontology_router
from workagent.backend.services import ontology_ai_service
from workagent.backend.services.ontology_ai_service import OntologyAIService

# Records the env it ran with into the Hermes home it was given, then fails like a real collect
# error would, so the scan job ends without needing the rest of the pipeline.
_FAKE_SCANNER = """\
import json, os, pathlib, sys
pathlib.Path(os.environ["HERMES_HOME"], "collect-env.json").write_text(json.dumps(dict(os.environ)))
sys.exit("collect stub")
"""

_FAKE_HERMES = """\
import json, os, pathlib, sys
pathlib.Path(os.environ["HERMES_HOME"], "judge-call.json").write_text(json.dumps(sys.argv[1:]))
print(json.dumps({"judgments": []}))
"""


@pytest.fixture
def profile_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "sandbox" / "profiles" / "ops"
    home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _files_under(root: Path) -> set[Path]:
    return {p for p in root.rglob("*") if p.is_file()}


def test_ontology_routes_keep_state_inside_the_active_hermes_home(profile_home: Path) -> None:
    scripts = profile_home / "skills" / "ontology" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "scanner_ai.py").write_text(_FAKE_SCANNER, encoding="utf-8")
    sandbox = profile_home.parents[1]
    before = _files_under(sandbox)

    app = FastAPI()
    app.include_router(build_ontology_router())
    with TestClient(app) as client:
        assert client.get("/api/ontology/scan/active").status_code == 404
        assert client.get("/api/ontology/scan/job-unknown").status_code == 404
        assert client.get("/api/ontology/overview").status_code == 404
        # Reads never create the artifacts tree in a profile that never scanned.
        assert _files_under(sandbox) == before
        assert not (profile_home / "artifacts").exists()

        job = client.post("/api/ontology/scan").json()
        finished = wait_until(
            lambda: (r := client.get(f"/api/ontology/scan/{job['job_id']}").json())["status"] == "failed" and r,
            what="scan job to fail on the stub collector",
        )

    assert finished["error"]
    job_file = profile_home / "artifacts" / "ontology" / "jobs" / f"{job['job_id']}.json"
    assert job_file.is_file()
    collect_env = json.loads((profile_home / "collect-env.json").read_text(encoding="utf-8"))
    assert collect_env["HERMES_HOME"] == str(profile_home)
    assert Path(collect_env["WORKAGENT_ONTOLOGY_SCANS_ROOT"]).is_relative_to(profile_home)
    assert all(p.is_relative_to(profile_home) for p in _files_under(sandbox))


@pytest.mark.platforms("posix")
def test_ai_judgment_runs_as_the_active_profile(
    profile_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_hermes = tmp_path / "bin" / "hermes"
    fake_hermes.parent.mkdir()
    fake_hermes.write_text(f"#!/usr/bin/env python3\n{_FAKE_HERMES}", encoding="utf-8")
    fake_hermes.chmod(0o755)
    monkeypatch.setenv("WORKAGENT_ONTOLOGY_HERMES_BIN", str(fake_hermes))
    bundle = profile_home / "scan" / "evidence-bundle.json"
    bundle.parent.mkdir()
    bundle.write_text(json.dumps({"objects": [{"object_id": "L3-1", "candidate_evidence": []}]}))

    OntologyAIService.judge(str(bundle))

    argv = json.loads((profile_home / "judge-call.json").read_text(encoding="utf-8"))
    assert "--profile" not in argv
