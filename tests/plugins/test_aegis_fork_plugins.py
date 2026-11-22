"""The fork's bundled plugins build only on the published plugin API.

ext-tools, rbac-guard and the aegis_* plugins import no Hermes name outside hermes_cli/data/plugin_api.json
(upstream's groups plus the "Aegis fork" groups) — what ``hermes plugins validate`` and
scripts/boundary_lint.py enforce. ext-tools loads through real bundled discovery once enabled; the aegis_*
plugins (``kind: backend``) load with no ``plugins.enabled`` entry, since they replace former core wiring.
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


# aegis_a2a's register() imports its implementation by absolute path on purpose: the loader runs the
# plugin as ``hermes_plugins.aegis_a2a``, and a relative import would give the Aegis/AISOC consoles
# (which read ``A2A_CONTEXT``) a second copy of the module's state.
_OWN_ABSOLUTE_IMPORTS = {"aegis_a2a": {"__init__.py plugins.aegis_a2a.delegate_tool"}}


@pytest.mark.parametrize("plugin", ["ext-tools", "rbac-guard", "aegis_a2a", "aegis_products", "aegis_source"])
def test_fork_plugin_imports_only_published_hermes_names(plugin):
    from hermes_cli.plugin_validate_private_imports import private_import_findings

    findings = {f"{site.split(':', 1)[0]} {site.split(' ', 1)[1]}" for site in private_import_findings(REPO / "plugins" / plugin)}
    assert findings == _OWN_ABSOLUTE_IMPORTS.get(plugin, set())


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


def test_aegis_plugins_load_without_being_enabled(hermes_home):
    from hermes_cli import plugins as pmod

    manager = pmod.PluginManager()
    manager.discover_and_load()

    for key in ("aegis_a2a", "aegis_products", "aegis_source"):
        assert manager._plugins[key].enabled, manager._plugins[key].error
    assert {"aegis_a2a_list", "aegis_a2a_delegate"} <= set(manager._plugins["aegis_a2a"].tools_registered)
    assert {"aegis", "aisoc", "workagent"} <= set(manager._cli_commands)
    assert "pre_llm_call" in manager._plugins["aegis_source"].hooks_registered


def test_an_operator_can_still_disable_an_aegis_plugin(hermes_home):
    import hermes_yaml as yaml
    from hermes_cli import plugins as pmod

    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump({"plugins": {"disabled": ["aegis_source"]}}), encoding="utf-8")
    manager = pmod.PluginManager()
    manager.discover_and_load()

    assert not manager._plugins["aegis_source"].enabled
