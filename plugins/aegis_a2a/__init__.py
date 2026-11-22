"""Aegis A2A delegation tools: ``aegis_a2a_list`` and ``aegis_a2a_delegate`` (toolset ``aegis_a2a``).

The implementation is ``plugins.aegis_a2a.delegate_tool``; it is imported absolutely (never
``from .delegate_tool``) so the plugin loader's synthetic package and the consoles that read
``A2A_CONTEXT`` share one module. The toolset stays opt-in per platform (``_DEFAULT_OFF_TOOLSETS``).
"""

from __future__ import annotations

# A foreground loop blocks on its input for up to 25 minutes per turn and may run many turns;
# the delegation supervises its own liveness (poll deadline, input timeout, /stop cancel), so the
# generic tool deadline must not abandon it mid-loop.
_DELEGATE_EXECUTION_TIMEOUT_SECONDS = 24 * 60 * 60.0


def register(ctx) -> None:
    from plugins.aegis_a2a import delegate_tool

    ctx.register_tool(
        name="aegis_a2a_list", toolset="aegis_a2a", schema=delegate_tool.A2A_LIST_SCHEMA,
        handler=delegate_tool.list_handler, description=delegate_tool.A2A_LIST_SCHEMA["description"],
        emoji="A2A",
    )
    ctx.register_tool(
        name="aegis_a2a_delegate", toolset="aegis_a2a", schema=delegate_tool.A2A_DELEGATE_SCHEMA,
        handler=delegate_tool.delegate_handler, description=delegate_tool.A2A_DELEGATE_SCHEMA["description"],
        emoji="🤝", execution_timeout=_DELEGATE_EXECUTION_TIMEOUT_SECONDS,
    )
