"""``ctx.session_id()``: the session a plugin command or hook is running for.

The classic CLI owns one interactive session, so a plugin command there (even before its first turn built
an agent) gets the CLI's id; elsewhere it is the id bound for the running turn.
"""
from __future__ import annotations

from types import SimpleNamespace

from gateway.session_context import scoped_current_session_id
from hermes_cli.plugin_session import context_session_id, context_surface


def test_cli_session_id_comes_from_the_cli_before_and_after_its_agent_exists():
    cli = SimpleNamespace(agent=None, session_id="cli-sess")
    manager = SimpleNamespace(_cli_ref=cli)
    assert context_surface(manager) == "cli"
    assert context_session_id(manager) == "cli-sess"

    cli.agent = SimpleNamespace(session_id="cli-sess-compressed")  # the agent's id wins once it exists
    assert context_session_id(manager) == "cli-sess-compressed"


def test_outside_the_cli_it_is_the_turns_bound_session():
    manager = SimpleNamespace(_cli_ref=None)
    with scoped_current_session_id("gw-sess"):
        assert context_session_id(manager) == "gw-sess"
