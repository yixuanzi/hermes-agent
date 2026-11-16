"""The fork's opt-in toolsets (``userenv``, ``aegis_a2a``) are configurable, off by default, and never core."""
from __future__ import annotations

import pytest

from hermes_cli.tools_config import CONFIGURABLE_TOOLSETS, _DEFAULT_OFF_TOOLSETS, _get_platform_tools
from toolsets import _HERMES_CORE_TOOLS, resolve_toolset


def test_userenv_and_aegis_a2a_appear_in_hermes_tools_and_start_off():
    keys = {key for key, _label, _desc in CONFIGURABLE_TOOLSETS}
    assert {"userenv", "aegis_a2a"} <= keys
    assert {"userenv", "aegis_a2a"} <= _DEFAULT_OFF_TOOLSETS


@pytest.mark.parametrize("toolset", ["userenv", "aegis_a2a"])
def test_an_explicit_platform_selection_enables_them(toolset):
    enabled = _get_platform_tools({"platform_toolsets": {"cli": [toolset]}}, "cli", include_default_mcp_servers=False)
    assert toolset in enabled


def test_a_default_platform_selection_leaves_them_off():
    enabled = _get_platform_tools({}, "cli", include_default_mcp_servers=False)
    assert not {"userenv", "aegis_a2a"} & set(enabled)


def test_the_toolsets_resolve_to_their_tools_and_stay_out_of_the_core_schema():
    assert set(resolve_toolset("aegis_a2a")) == {"aegis_a2a_list", "aegis_a2a_delegate"}
    assert set(resolve_toolset("userenv")) == {"userenv"}
    assert not {"aegis_a2a_list", "aegis_a2a_delegate", "userenv"} & set(_HERMES_CORE_TOOLS)
