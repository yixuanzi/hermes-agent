"""``hermes aegis`` / ``hermes aisoc`` / ``hermes workagent``: the Aegis fork's product consoles.

Each product owns its argument parser and startup (``<product>/backend/main.py``); this module only
attaches them and delegates. Backends import lazily, so building the CLI parser stays cheap.
"""

from __future__ import annotations

import importlib
from typing import Callable

# (command, help, description); the backend module is ``<command>.backend.main`` and exposes
# ``configure_<command>_parser(parser)`` plus ``cmd_<command>(args)``.
_PRODUCTS = (
    ("aegis", "Start the Aegis web console",
     "Launch the Aegis console for agent orchestration and routing policy controls"),
    ("aisoc", "Start the AISOC web console",
     "Launch the AISOC console for chat operations and runtime controls"),
    ("workagent", "Start the WorkAgent web console",
     "Launch the WorkAgent console for reusable workflow operations and runtime controls"),
)

PRODUCT_COMMANDS = frozenset(name for name, _help, _description in _PRODUCTS)


def _backend(name: str):
    return importlib.import_module(f"{name}.backend.main")


def cmd_aegis(args):
    """Delegate Aegis startup to its backend entrypoint."""
    return _backend("aegis").cmd_aegis(args)


def cmd_aisoc(args):
    """Delegate AISOC startup and module dispatch to its backend entrypoint."""
    return _backend("aisoc").cmd_aisoc(args)


def cmd_workagent(args):
    """Delegate WorkAgent startup and module dispatch to its backend entrypoint."""
    return _backend("workagent").cmd_workagent(args)


def build_product_parsers(subparsers, *, commands: dict[str, Callable]) -> None:
    """Attach each product's parser; ``commands`` maps the command name to its handler."""
    for name, help_text, description in _PRODUCTS:
        parser = subparsers.add_parser(name, help=help_text, description=description)
        getattr(_backend(name), f"configure_{name}_parser")(parser)
        parser.set_defaults(func=commands[name])
