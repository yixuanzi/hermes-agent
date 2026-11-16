"""The fork's bundled plugins build only on the published plugin API.

ext-tools and rbac-guard import no Hermes name outside hermes_cli/data/plugin_api.json (upstream's groups plus
the "Aegis fork" groups) — what ``hermes plugins validate`` and scripts/boundary_lint.py enforce — and
ext-tools loads through real bundled discovery as a package, with every command, tool and hook registered.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


@pytest.mark.parametrize("plugin", ["ext-tools", "rbac-guard"])
def test_fork_plugin_imports_only_published_hermes_names(plugin):
    from hermes_cli.plugin_validate_private_imports import private_import_findings

    assert private_import_findings(REPO / "plugins" / plugin) == []


def test_ext_tools_loads_through_bundled_discovery(hermes_home):
    import hermes_yaml as yaml
    from hermes_cli import plugins as pmod

    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump({"plugins": {"enabled": ["ext-tools"]}}), encoding="utf-8"
    )
    manager = pmod.PluginManager()
    manager.discover_and_load()

    loaded = manager._plugins["ext-tools"]
    assert loaded.enabled, loaded.error
    assert {"userenv", "codex", "claude"} <= set(loaded.commands_registered)
    assert "cron_prompt" in loaded.tools_registered
    assert {"pre_llm_call", "pre_gateway_dispatch", "on_session_finalize"} <= set(loaded.hooks_registered)
