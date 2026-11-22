"""The fork's opt-in toolsets are configurable, off by default, and never core.

``userenv`` is a shipped toolset; ``aegis_a2a`` is the bundled ``aegis_a2a`` plugin's toolset, which
auto-loads (``kind: backend``) and shows in ``hermes tools`` as a plugin toolset.
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
    from hermes_cli.plugins import discover_plugins

    discover_plugins(force=True)


def test_userenv_and_aegis_a2a_appear_in_hermes_tools_and_start_off(plugins_loaded):
    from hermes_cli.tools_config import _get_plugin_toolset_keys

    assert "userenv" in {key for key, _label, _desc in CONFIGURABLE_TOOLSETS}
    assert "aegis_a2a" in _get_plugin_toolset_keys()
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
