from __future__ import annotations

from pathlib import Path

from aisoc.backend.services.skill_installer import (
    MANAGED_MARKER,
    bundled_skills_root,
    install_bundled_skills,
)


def _make_source_skill(root: Path, name: str, body: str = "v1") -> Path:
    skill = root / name
    (skill / "assets").mkdir(parents=True)
    (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\n{body}\n", encoding="utf-8")
    (skill / "assets" / "bridge.js").write_text(f"// {body}\n", encoding="utf-8")
    return skill


def test_installs_new_skill_with_managed_marker(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    skills_root = tmp_path / "skills"
    _make_source_skill(source_root, "html-deliverable")

    installed = install_bundled_skills(source_root=source_root, skills_root=skills_root)

    assert installed == ["html-deliverable"]
    target = skills_root / "html-deliverable"
    assert (target / "SKILL.md").is_file()
    assert (target / "assets" / "bridge.js").is_file()
    assert (target / MANAGED_MARKER).is_file()


def test_reinstall_updates_managed_skill_only_when_changed(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    skills_root = tmp_path / "skills"
    source = _make_source_skill(source_root, "html-deliverable")

    assert install_bundled_skills(source_root=source_root, skills_root=skills_root)
    assert install_bundled_skills(source_root=source_root, skills_root=skills_root) == []

    (source / "SKILL.md").write_text("---\nname: html-deliverable\n---\nv2\n", encoding="utf-8")
    assert install_bundled_skills(source_root=source_root, skills_root=skills_root) == [
        "html-deliverable"
    ]
    assert "v2" in (skills_root / "html-deliverable" / "SKILL.md").read_text(encoding="utf-8")


def test_never_overwrites_operator_owned_skill(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    skills_root = tmp_path / "skills"
    _make_source_skill(source_root, "html-deliverable", body="bundled")

    operator_skill = skills_root / "html-deliverable"
    operator_skill.mkdir(parents=True)
    (operator_skill / "SKILL.md").write_text("operator custom\n", encoding="utf-8")

    assert install_bundled_skills(source_root=source_root, skills_root=skills_root) == []
    assert (operator_skill / "SKILL.md").read_text(encoding="utf-8") == "operator custom\n"
    assert not (operator_skill / MANAGED_MARKER).exists()


def test_bundled_root_contains_html_deliverable() -> None:
    assert (bundled_skills_root() / "html-deliverable" / "SKILL.md").is_file()
