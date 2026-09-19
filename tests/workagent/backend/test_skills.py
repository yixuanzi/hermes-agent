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


def _patch_disabled_skills_store(monkeypatch: pytest.MonkeyPatch, initial: set[str]) -> dict[str, set[str]]:
    captured: dict[str, set[str]] = {"disabled": set(initial)}
    monkeypatch.setattr(skill_service, "load_config", lambda: {})
    monkeypatch.setattr(
        "hermes_cli.skills_config.get_disabled_skills",
        lambda config, platform=None: set(captured["disabled"]),
    )

    def fake_save(config: dict, disabled: set[str], platform: str | None = None) -> None:
        captured["disabled"] = set(disabled)

    monkeypatch.setattr("hermes_cli.skills_config.save_disabled_skills", fake_save)
    return captured


def test_toggle_category_disables_every_skill_in_that_category_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = {
        "alpha": {"name": "alpha", "category": "aegis", "path": "/skills/aegis/alpha"},
        "beta": {"name": "beta", "category": "aegis", "path": "/skills/aegis/beta"},
        "gamma": {"name": "gamma", "category": "other", "path": "/skills/other/gamma"},
    }
    monkeypatch.setattr(skill_service, "_scan_skill_index", lambda: index)
    captured = _patch_disabled_skills_store(monkeypatch, set())

    result = skill_service.toggle_category("aegis", False)

    assert result == {"ok": True, "category": "aegis", "enabled": False, "names": ["alpha", "beta"]}
    assert captured["disabled"] == {"alpha", "beta"}


def test_toggle_category_enabling_only_clears_that_categorys_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = {
        "alpha": {"name": "alpha", "category": "aegis", "path": "/skills/aegis/alpha"},
        "gamma": {"name": "gamma", "category": "other", "path": "/skills/other/gamma"},
    }
    monkeypatch.setattr(skill_service, "_scan_skill_index", lambda: index)
    captured = _patch_disabled_skills_store(monkeypatch, {"alpha", "gamma"})

    result = skill_service.toggle_category("aegis", True)

    assert result == {"ok": True, "category": "aegis", "enabled": True, "names": ["alpha"]}
    assert captured["disabled"] == {"gamma"}


def test_toggle_category_treats_blank_category_as_misc(monkeypatch: pytest.MonkeyPatch) -> None:
    index = {"solo": {"name": "solo", "category": None, "path": "/skills/solo"}}
    monkeypatch.setattr(skill_service, "_scan_skill_index", lambda: index)
    captured = _patch_disabled_skills_store(monkeypatch, set())

    result = skill_service.toggle_category("misc", False)

    assert result["names"] == ["solo"]
    assert captured["disabled"] == {"solo"}


def test_toggle_category_rejects_unknown_category(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(skill_service, "_scan_skill_index", lambda: {})

    with pytest.raises(skill_service.SkillNotFoundError, match="nonexistent"):
        skill_service.toggle_category("nonexistent", True)


def test_toggle_category_route_returns_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    app = FastAPI()
    app.include_router(build_skills_router())
    monkeypatch.setattr(
        skill_service,
        "toggle_category",
        lambda category, enabled: {"ok": True, "category": category, "enabled": enabled, "names": ["alpha"]},
    )

    with TestClient(app) as client:
        response = client.put("/api/skills/toggle-category", json={"category": "aegis", "enabled": False})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "category": "aegis", "enabled": False, "names": ["alpha"]}


def test_toggle_category_route_maps_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    app = FastAPI()
    app.include_router(build_skills_router())
    monkeypatch.setattr(
        skill_service,
        "toggle_category",
        lambda category, enabled: (_ for _ in ()).throw(skill_service.SkillNotFoundError("none")),
    )

    with TestClient(app) as client:
        response = client.put("/api/skills/toggle-category", json={"category": "missing", "enabled": True})

    assert response.status_code == 404


def test_save_skill_content_overwrites_skill_md(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    skill_dir = _make_skill(tmp_path / "skills" / "demo-category", "demo")
    _patch_skill_entry(monkeypatch, skill_dir, "demo", tmp_path / "skills")

    result = skill_service.save_skill_content("demo", "---\nname: demo\n---\n\nUpdated body\n")

    assert result["ok"] is True
    assert result["name"] == "demo"
    assert result["path"] == "SKILL.md"
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "---\nname: demo\n---\n\nUpdated body\n"


def test_save_skill_content_rejects_oversized_content(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    skill_dir = _make_skill(tmp_path / "skills" / "demo-category", "demo")
    _patch_skill_entry(monkeypatch, skill_dir, "demo", tmp_path / "skills")
    monkeypatch.setattr(skill_service, "_MAX_SKILL_FILE_SIZE", 10)

    with pytest.raises(ValueError, match="too large"):
        skill_service.save_skill_content("demo", "this body is definitely longer than 10 bytes")

    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8").startswith("---")


def test_save_skill_content_requires_existing_skill_md(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "demo-category" / "demo"
    skill_dir.mkdir(parents=True)
    _patch_skill_entry(monkeypatch, skill_dir, "demo", tmp_path / "skills")

    with pytest.raises(skill_service.SkillNotFoundError):
        skill_service.save_skill_content("demo", "content")


def test_save_skill_appendix_content_overwrites_appendix_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    skill_dir = _make_skill(tmp_path / "skills" / "demo-category", "demo")
    _patch_skill_entry(monkeypatch, skill_dir, "demo", tmp_path / "skills")

    result = skill_service.save_skill_appendix_content("demo", "references/notes.md", "updated notes")

    assert result == {
        "ok": True,
        "name": "notes.md",
        "path": "references/notes.md",
        "size": len(b"updated notes"),
        "modified": result["modified"],
    }
    assert (skill_dir / "references" / "notes.md").read_text(encoding="utf-8") == "updated notes"


def test_save_skill_appendix_content_rejects_traversal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    skill_dir = _make_skill(tmp_path / "skills" / "demo-category", "demo")
    _patch_skill_entry(monkeypatch, skill_dir, "demo", tmp_path / "skills")

    with pytest.raises(ValueError, match="traversal"):
        skill_service.save_skill_appendix_content("demo", "../outside.md", "content")


def test_save_skill_appendix_content_rejects_skill_md_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    skill_dir = _make_skill(tmp_path / "skills" / "demo-category", "demo")
    _patch_skill_entry(monkeypatch, skill_dir, "demo", tmp_path / "skills")

    with pytest.raises(ValueError, match="not an appendix file"):
        skill_service.save_skill_appendix_content("demo", "SKILL.md", "content")


def test_save_skill_appendix_content_requires_existing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    skill_dir = _make_skill(tmp_path / "skills" / "demo-category", "demo")
    _patch_skill_entry(monkeypatch, skill_dir, "demo", tmp_path / "skills")

    with pytest.raises(FileNotFoundError):
        skill_service.save_skill_appendix_content("demo", "references/missing.md", "content")


@pytest.mark.parametrize(
    ("exception", "status_code"),
    [
        (skill_service.SkillNotFoundError("missing"), 404),
        (ValueError("bad content"), 400),
        (skill_service.SkillWriteFailedError("disk full"), 500),
    ],
)
def test_put_skill_content_route_maps_errors(
    monkeypatch: pytest.MonkeyPatch,
    exception: Exception,
    status_code: int,
) -> None:
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    app = FastAPI()
    app.include_router(build_skills_router())
    monkeypatch.setattr(
        skill_service,
        "save_skill_content",
        lambda name, content: (_ for _ in ()).throw(exception),
    )

    with TestClient(app) as client:
        response = client.put("/api/skills/demo", json={"content": "new body"})

    assert response.status_code == status_code


def test_put_skill_content_route_returns_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    app = FastAPI()
    app.include_router(build_skills_router())
    monkeypatch.setattr(
        skill_service,
        "save_skill_content",
        lambda name, content: {"ok": True, "name": name, "path": "SKILL.md", "size": len(content), "modified": 0},
    )

    with TestClient(app) as client:
        response = client.put("/api/skills/demo", json={"content": "new body"})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "name": "demo", "path": "SKILL.md", "size": 8, "modified": 0}


@pytest.mark.parametrize(
    ("exception", "status_code"),
    [
        (skill_service.SkillNotFoundError("missing"), 404),
        (FileNotFoundError("missing file"), 404),
        (ValueError("bad path"), 400),
        (skill_service.SkillWriteFailedError("disk full"), 500),
    ],
)
def test_put_skill_appendix_route_maps_errors(
    monkeypatch: pytest.MonkeyPatch,
    exception: Exception,
    status_code: int,
) -> None:
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    app = FastAPI()
    app.include_router(build_skills_router())
    monkeypatch.setattr(
        skill_service,
        "save_skill_appendix_content",
        lambda name, path, content: (_ for _ in ()).throw(exception),
    )

    with TestClient(app) as client:
        response = client.put(
            "/api/skills/demo/appendix",
            json={"path": "references/notes.md", "content": "updated"},
        )

    assert response.status_code == status_code


def test_toggle_and_toggle_category_routes_are_not_shadowed_by_generic_skill_put(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: the generic PUT "/{skill_name}" route is registered
    after the literal "/toggle" and "/toggle-category" routes, so a request
    to those literal paths must still resolve to the toggle handlers instead
    of being treated as saving a skill named "toggle"/"toggle-category".
    """
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    app = FastAPI()
    app.include_router(build_skills_router())
    monkeypatch.setattr(skill_service, "toggle_skill", lambda name, enabled: {"ok": True, "via": "toggle_skill"})
    monkeypatch.setattr(
        skill_service, "toggle_category", lambda category, enabled: {"ok": True, "via": "toggle_category"}
    )
    monkeypatch.setattr(
        skill_service,
        "save_skill_content",
        lambda name, content: (_ for _ in ()).throw(AssertionError("should not be called for /toggle paths")),
    )

    with TestClient(app) as client:
        toggle_response = client.put("/api/skills/toggle", json={"name": "demo", "enabled": False})
        category_response = client.put("/api/skills/toggle-category", json={"category": "demo", "enabled": False})

    assert toggle_response.status_code == 200
    assert toggle_response.json() == {"ok": True, "via": "toggle_skill"}
    assert category_response.status_code == 200
    assert category_response.json() == {"ok": True, "via": "toggle_category"}
