"""Load plugins/ext-tools the way the plugin loader does: as the package ``hermes_plugins.ext_tools``,
whose modules import each other package-relatively. The tests here import those modules by their
bare names, so each is also exposed under it (the same module object the plugin itself uses)."""
from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[3] / "plugins" / "ext-tools"
PACKAGE = "hermes_plugins.ext_tools"
_MODULES = ("raw_prompt", "tool_record", "claude_acp", "codex_cmd", "claude_cmd", "cron_prompt", "userenv_cmd")


def load_ext_tools() -> types.ModuleType:
    """The ``hermes_plugins.ext_tools`` package, imported once per process."""
    if PACKAGE in sys.modules:
        return sys.modules[PACKAGE]
    if "hermes_plugins" not in sys.modules:
        namespace = types.ModuleType("hermes_plugins")
        namespace.__path__ = []
        sys.modules["hermes_plugins"] = namespace
    spec = importlib.util.spec_from_file_location(
        PACKAGE, PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)]
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = package
    spec.loader.exec_module(package)
    return package


load_ext_tools()
for _name in _MODULES:
    sys.modules.setdefault(_name, importlib.import_module(f"{PACKAGE}.{_name}"))
