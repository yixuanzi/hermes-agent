"""The fork's opt-in toolsets are configurable, off by default, and never core.

Both are plugin toolsets shown in ``hermes tools``: ``userenv`` comes from the ``ext-tools`` plugin (enabled
in ``plugins.enabled``), ``aegis_a2a`` from the bundled ``aegis_a2a`` plugin, which auto-loads (``kind: backend``).
"""
from __future__ import annotations

import pytest

from hermes_cli.tools_config import CONFIGURABLE_TOOLSETS, _DEFAULT_OFF_TOOLSETS, _get_platform_tools
from toolsets import _HERMES_CORE_TOOLS, resolve_toolset


@pytest.fixture
def plugins_loaded(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    import hermes_yaml as yaml
    from hermes_cli.plugins import discover_plugins

    (home / "config.yaml").write_text(yaml.safe_dump({"plugins": {"enabled": ["ext-tools"]}}), encoding="utf-8")

    discover_plugins(force=True)


def test_userenv_and_aegis_a2a_appear_in_hermes_tools_and_start_off(plugins_loaded):
    from hermes_cli.tools_config import _get_plugin_toolset_keys

    assert {"userenv", "aegis_a2a"} <= _get_plugin_toolset_keys()
    assert not {"userenv", "aegis_a2a"} & {key for key, _label, _desc in CONFIGURABLE_TOOLSETS}
    assert {"userenv", "aegis_a2a"} <= _DEFAULT_OFF_TOOLSETS


@pytest.mark.parametrize("toolset", ["userenv", "aegis_a2a"])
def test_an_explicit_platform_selection_enables_them(plugins_loaded, toolset):
    enabled = _get_platform_tools({"platform_toolsets": {"cli": [toolset]}}, "cli", include_default_mcp_servers=False)
    assert toolset in enabled


def test_a_default_platform_selection_leaves_them_off(plugins_loaded):
    enabled = _get_platform_tools({}, "cli", include_default_mcp_servers=False)
    assert not {"userenv", "aegis_a2a"} & set(enabled)


def test_the_toolsets_resolve_to_their_tools_and_stay_out_of_the_core_schema(plugins_loaded):
    assert set(resolve_toolset("aegis_a2a")) == {"aegis_a2a_list", "aegis_a2a_delegate"}
    assert set(resolve_toolset("userenv")) == {"userenv"}
    assert not {"aegis_a2a_list", "aegis_a2a_delegate", "userenv"} & set(_HERMES_CORE_TOOLS)
