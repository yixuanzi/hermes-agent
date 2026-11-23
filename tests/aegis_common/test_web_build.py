"""Behavior tests for the consoles' shared frontend build."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from aegis_common import web_build


def _frontend(tmp_path: Path) -> tuple[Path, Path]:
    web_dir = tmp_path / "frontend"
    (web_dir / "src").mkdir(parents=True)
    (web_dir / "package.json").write_text("{}", encoding="utf-8")
    (web_dir / "src" / "main.tsx").write_text("export {};\n", encoding="utf-8")
    dist_dir = tmp_path / "web_dist"
    return web_dir, dist_dir


def _age(path: Path, seconds: float) -> None:
    stamp = path.stat().st_mtime - seconds
    os.utime(path, (stamp, stamp))


def _fake_npm(tmp_path: Path, build_script: str) -> str:
    """An ``npm`` whose ``run build`` runs ``build_script``; every other command succeeds."""
    npm = tmp_path / "npm"
    npm.write_text(
        '#!/bin/sh\nif [ "$1" = run ]; then\n' + build_script + "\nfi\nexit 0\n",
        encoding="utf-8",
    )
    npm.chmod(0o755)
    return str(npm)


def test_the_vite_entry_html_counts_as_a_source(tmp_path: Path) -> None:
    web_dir, dist_dir = _frontend(tmp_path)
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    _age(web_dir / "src" / "main.tsx", 60)
    (web_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    _age(dist_dir / "index.html", 30)

    assert web_build.web_ui_build_needed(web_dir, dist_dir)


def test_no_frontend_means_nothing_to_build(tmp_path: Path) -> None:
    assert not web_build.web_ui_build_needed(tmp_path / "missing", tmp_path / "web_dist")
    assert web_build.build_web_ui(tmp_path / "missing", tmp_path / "web_dist", label="X") is False


@pytest.mark.platforms("posix")
def test_a_failed_build_raises_with_npm_output_and_keeps_the_old_dist(
    monkeypatch, tmp_path: Path
) -> None:
    web_dir, dist_dir = _frontend(tmp_path)
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("old bundle", encoding="utf-8")
    _age(dist_dir / "index.html", 60)
    npm = _fake_npm(tmp_path, "echo 'src/main.tsx(1,1): error TS2322' >&2; exit 2")
    monkeypatch.setattr(web_build, "find_node_executable", lambda _name: npm)

    with pytest.raises(web_build.WebBuildError) as raised:
        web_build.build_web_ui(web_dir, dist_dir, label="AISOC")

    assert "`npm run build` exited with 2" in str(raised.value)
    assert "error TS2322" in raised.value.output
    assert (dist_dir / "index.html").read_text(encoding="utf-8") == "old bundle"


@pytest.mark.platforms("posix")
def test_npm_runs_with_the_environment_it_is_given(monkeypatch, tmp_path: Path) -> None:
    web_dir, dist_dir = _frontend(tmp_path)
    npm = _fake_npm(tmp_path, f'mkdir -p "{dist_dir}"; printf %s "$HOME" > "{dist_dir}/index.html"')
    monkeypatch.setattr(web_build, "find_node_executable", lambda _name: npm)
    launch_env = {**os.environ, "HOME": str(tmp_path / "user-home")}

    assert web_build.build_web_ui(web_dir, dist_dir, label="AISOC", env=launch_env) is True

    assert (dist_dir / "index.html").read_text(encoding="utf-8") == str(tmp_path / "user-home")
    assert web_build.build_web_ui(web_dir, dist_dir, label="AISOC", env=launch_env) is False
