from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import aisoc.backend.services.ontology_ai_service as _ontology_ai_module
import aisoc.backend.services.user_store as _user_store_module
from aisoc.backend.config import load_aisoc_settings
from aisoc.backend.server import create_app
from aisoc.backend.services.ontology_job_service import OntologyJobService
from aisoc.backend.services.ontology_service import OntologyService


BOOTSTRAP_PASSWORD = "test-admin-password-123"


@pytest.fixture(autouse=True)
def _isolate_aisoc_ontology_paths(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The ontology services pin their artifact paths to the OS user's real
    # ~/.hermes/profiles/aisoc at import time (by design, so a rewritten HOME
    # cannot move them). Every app build reaps that job root, so point all of
    # them at the test's temp dir instead of the developer's live profile:
    # the env overrides cover tests that re-import aisoc.backend, the
    # attribute patches cover the classes this conftest already imported.
    profile = tmp_path / "aisoc-profile"
    ontology = profile / "artifacts" / "ontology"
    standard_v3 = ontology / "standard" / "standard-graph_v3.json"
    paths = {
        "AISOC_ONTOLOGY_STANDARD_GRAPH_V3": ("STANDARD_GRAPH_V3", standard_v3),
        "AISOC_ONTOLOGY_STANDARD_GRAPH": ("STANDARD_GRAPH_ALIAS", ontology / "standard" / "standard-graph.json"),
        "AISOC_ONTOLOGY_SCANS_ROOT": ("SCANS_ROOT", ontology / "scans"),
        "AISOC_ONTOLOGY_SKILL_SCRIPTS": ("SKILL_SCRIPTS", profile / "skills" / "ontology" / "scripts"),
    }
    for env_name, (attr, path) in paths.items():
        monkeypatch.setenv(env_name, str(path))
        monkeypatch.setattr(OntologyService, attr, path)
    monkeypatch.setattr(OntologyService, "STANDARD_GRAPH", standard_v3)
    monkeypatch.setenv("AISOC_ONTOLOGY_JOBS_ROOT", str(ontology / "jobs"))
    monkeypatch.setattr(OntologyJobService, "JOB_ROOT", ontology / "jobs")
    monkeypatch.setattr(_ontology_ai_module, "_PROFILE", str(profile))
    monkeypatch.setattr(_ontology_ai_module, "_real_home", lambda: str(tmp_path / "os-home"))


@pytest.fixture
def test_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AISOC_BOOTSTRAP_ADMIN_PASSWORD", BOOTSTRAP_PASSWORD)
    monkeypatch.setenv("AISOC_JWT_SECRET", "test-jwt-secret-1234567890-abcdef")
    # The user store is a process-wide singleton; reset it so each test's
    # fresh HERMES_HOME tempdir gets its own store instead of inheriting
    # mutated state (e.g. a changed admin password) from an earlier test.
    monkeypatch.setattr(_user_store_module, "_STORE", None)
    settings = load_aisoc_settings()
    app = create_app(settings)
    return TestClient(app)


@pytest.fixture
def auth_token(test_client: TestClient) -> str:
    response = test_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": BOOTSTRAP_PASSWORD},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    assert isinstance(token, str) and token
    return token


@pytest.fixture
def auth_headers(auth_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth_token}"}

