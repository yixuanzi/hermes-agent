from __future__ import annotations

from pathlib import Path

import pytest

from workagent.backend.routes.skills import build_skills_router
from workagent.backend.services import skill_service


def _make_skill(root: Path, name: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "notes.md").write_text("notes", encoding="utf-8")
    return skill_dir


def _patch_skill_entry(
    monkeypatch: pytest.MonkeyPatch,
    skill_dir: Path,
    name: str,
    local_root: Path,
) -> None:
    monkeypatch.setattr(
        skill_service,
        "_resolve_skill_entry",
        lambda requested_name: {"name": requested_name, "path": str(skill_dir)},
    )
    import tools.skills_tool as skills_tool

    monkeypatch.setattr(skills_tool, "SKILLS_DIR", local_root)


def test_delete_skill_removes_package_and_empty_category(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    local_root = tmp_path / "skills"
    category_dir = local_root / "docs"
    skill_dir = _make_skill(category_dir, "demo")
    _patch_skill_entry(monkeypatch, skill_dir, "demo", local_root)

    result = skill_service.delete_skill("demo")

    assert result == {"ok": True, "name": "demo"}
    assert not skill_dir.exists()
    assert not category_dir.exists()
    assert local_root.exists()


def test_delete_skill_rejects_external_skill(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    local_root = tmp_path / "skills"
    external_root = tmp_path / "external-skills"
    skill_dir = _make_skill(external_root, "external-demo")
    _patch_skill_entry(monkeypatch, skill_dir, "external-demo", local_root)

    with pytest.raises(
        skill_service.SkillDeleteForbiddenError,
        match="current profile's local skills directory",
    ):
        skill_service.delete_skill("external-demo")

    assert skill_dir.exists()
    assert not local_root.exists()


def test_delete_skill_rejects_skills_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    local_root = tmp_path / "skills"
    local_root.mkdir()
    _patch_skill_entry(monkeypatch, local_root, "root", local_root)

    with pytest.raises(
        skill_service.SkillDeleteForbiddenError,
        match="skills root itself",
    ):
        skill_service.delete_skill("root")


def test_delete_skill_rejects_organisation_mirror(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    local_root = tmp_path / "skills"
    skill_dir = _make_skill(local_root / "_org" / "acme", "shared-demo")
    _patch_skill_entry(monkeypatch, skill_dir, "shared-demo", local_root)

    with pytest.raises(
        skill_service.SkillDeleteForbiddenError,
        match="organisation-shared",
    ):
        skill_service.delete_skill("shared-demo")

    assert skill_dir.exists()


def test_delete_skill_rejects_symlinked_skill(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    local_root = tmp_path / "skills"
    outside_dir = _make_skill(tmp_path / "outside", "linked-demo")
    local_root.mkdir()
    link = local_root / "linked-demo"
    try:
        link.symlink_to(outside_dir, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    _patch_skill_entry(monkeypatch, link, "linked-demo", local_root)

    with pytest.raises(
        skill_service.SkillDeleteForbiddenError,
        match="symlink or junction",
    ):
        skill_service.delete_skill("linked-demo")

    assert outside_dir.exists()


def test_delete_skills_route_returns_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    app = FastAPI()
    app.include_router(build_skills_router())
    monkeypatch.setattr(
        skill_service,
        "delete_skill",
        lambda name: {"ok": True, "name": name},
    )

    with TestClient(app) as client:
        response = client.delete("/api/skills/demo")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "name": "demo"}


@pytest.mark.parametrize(
    ("exception", "status_code"),
    [
        (skill_service.SkillNotFoundError("missing"), 404),
        (skill_service.SkillDeleteForbiddenError("protected"), 403),
        (skill_service.SkillDeleteFailedError("failed"), 500),
    ],
)
def test_delete_skills_route_maps_delete_errors(
    monkeypatch: pytest.MonkeyPatch,
    exception: Exception,
    status_code: int,
) -> None:
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    app = FastAPI()
    app.include_router(build_skills_router())
    monkeypatch.setattr(skill_service, "delete_skill", lambda name: (_ for _ in ()).throw(exception))

    with TestClient(app) as client:
        response = client.delete("/api/skills/demo")

    assert response.status_code == status_code
