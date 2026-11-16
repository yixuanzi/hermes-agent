"""Bind the caller's user-env identity around gateway work that runs outside a tool call (fork).

Plugin slash commands run before any agent turn, so identity-dependent commands (``/userenv``)
would otherwise see no caller. Identity comes only from the adapter's ``SessionSource``, never from
message text, so it cannot be forged; the ContextVar is reset when the scope exits.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator


@contextmanager
def caller_user_env_identity(source: Any) -> Iterator[None]:
    from tools.user_env_runtime import reset_current_user_env_identity, set_current_user_env_identity

    platform = getattr(getattr(source, "platform", None), "value", None) or ""
    token = set_current_user_env_identity(
        platform, getattr(source, "user_id", None), getattr(source, "user_name", None))
    try:
        yield
    finally:
        reset_current_user_env_identity(token)
