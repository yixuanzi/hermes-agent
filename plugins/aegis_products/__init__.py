"""``hermes aegis`` / ``hermes aisoc`` / ``hermes workagent``: the Aegis fork's product consoles.

Each product owns its argument parser and startup (``<product>/backend/main.py``); this plugin only
attaches them through ``ctx.register_cli_command`` and delegates, so ``hermes_cli/main.py`` stays
upstream's. Backends import lazily: ``setup_fn`` runs only when the plugin CLI tree is built, and a
handler imports its backend only when that command runs.
"""

from __future__ import annotations

import importlib

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


def _setup(name: str):
    def setup(parser) -> None:
        getattr(_backend(name), f"configure_{name}_parser")(parser)
    return setup


def _handler(name: str):
    def handler(args):
        return getattr(_backend(name), f"cmd_{name}")(args)
    handler.__name__ = f"cmd_{name}"
    return handler


def register(ctx) -> None:
    for name, help_text, description in _PRODUCTS:
        ctx.register_cli_command(name=name, help=help_text, setup_fn=_setup(name), handler_fn=_handler(name),
                                 description=description)
