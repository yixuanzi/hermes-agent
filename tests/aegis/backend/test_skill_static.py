from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aegis.backend.routes.skills_static import build_skills_static_router


@pytest.fixture
def skill_asset_client(tmp_path: Path) -> TestClient:
    skills_root = tmp_path / "aegis" / "skills"
    bridge = skills_root / "html-deliverable" / "assets" / "agent2ui-bridge.js"
    bridge.parent.mkdir(parents=True)
    bridge.write_text('console.log("bridge");\n', encoding="utf-8")
    (skills_root / "html-deliverable" / "SKILL.md").write_text("not public", encoding="utf-8")

    app = FastAPI()
    app.include_router(build_skills_static_router(skills_root))
    return TestClient(app)


def test_serves_public_skill_javascript_with_cache_headers(skill_asset_client: TestClient) -> None:
    response = skill_asset_client.get("/static/skills/html-deliverable/assets/agent2ui-bridge.js")

    assert response.status_code == 200
    assert response.text == 'console.log("bridge");\n'
    assert response.headers["content-type"].startswith("application/javascript")
    assert response.headers["cache-control"] == "public, max-age=3600"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "path",
    [
        "/static/skills/html-deliverable/SKILL.md",
        "/static/skills/html-deliverable/assets",
        "/static/skills/html-deliverable/assets/missing.js",
        "/static/skills/%2Ftmp%2Foutside.js",
        "/static/skills/%2e%2e/secret.js",
        "/static/skills/html-deliverable/assets/%2e%2e/%2e%2e/SKILL.md",
    ],
)
def test_rejects_non_javascript_and_traversal_paths(skill_asset_client: TestClient, path: str) -> None:
    assert skill_asset_client.get(path).status_code == 404


def test_rejects_symlinked_javascript_outside_skills_root(tmp_path: Path) -> None:
    skills_root = tmp_path / "aegis" / "skills"
    assets = skills_root / "html-deliverable" / "assets"
    assets.mkdir(parents=True)
    outside_file = tmp_path / "outside.js"
    outside_file.write_text("secret", encoding="utf-8")
    (assets / "outside.js").symlink_to(outside_file)

    app = FastAPI()
    app.include_router(build_skills_static_router(skills_root))
    with TestClient(app) as client:
        assert client.get("/static/skills/html-deliverable/assets/outside.js").status_code == 404


def test_rejects_javascript_reached_through_an_internal_symlink(tmp_path: Path) -> None:
    skills_root = tmp_path / "aegis" / "skills"
    assets = skills_root / "html-deliverable" / "assets"
    assets.mkdir(parents=True)
    (assets / "bridge.js").write_text("bridge", encoding="utf-8")
    (skills_root / "html-deliverable" / "alias").symlink_to(assets, target_is_directory=True)

    app = FastAPI()
    app.include_router(build_skills_static_router(skills_root))
    with TestClient(app) as client:
        assert client.get("/static/skills/html-deliverable/alias/bridge.js").status_code == 404


def test_server_exposes_the_bridge_without_bearer_auth(client: TestClient) -> None:
    response = client.get("/static/skills/html-deliverable/assets/agent2ui-bridge.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert 'const channel = "aegis-agent2ui"' in response.text
