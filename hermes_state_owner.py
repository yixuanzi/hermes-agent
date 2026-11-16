"""Session ownership for callers with their own per-user account system (the AISOC console).

``_ensure_db_session`` creates rows with ``user_id=None``; such a caller attaches its account to the
row after the fact (``set_session_user_id``, typically every turn so it self-heals when the row did
not exist yet) and scopes listings to it (``list_sessions_rich`` / ``session_count`` ``user_id``).
"""

from __future__ import annotations

from typing import Any, Optional

# Ownerless rows: legacy rows and rows from platforms with no per-user account concept
# (tui/discord/cron). A shared listing still surfaces that history; a private one does not.
_OWNERLESS_SQL = "s.user_id IS NULL OR s.user_id = ''"


def owner_filter(user_id: Optional[str], include_ownerless: bool) -> Optional[tuple[str, bool]]:
    """The ``owner`` argument for ``_session_filter_where``: None means no ownership filter."""
    return (user_id, include_ownerless) if user_id else None


def owner_clause(owner: tuple[str, bool]) -> tuple[str, list[Any]]:
    """WHERE clause for rows owned by ``owner[0]``, plus ownerless rows when ``owner[1]``."""
    user_id, include_ownerless = owner
    if include_ownerless:
        return f"(s.user_id = ? OR {_OWNERLESS_SQL})", [user_id]
    return "s.user_id = ?", [user_id]


class SessionOwnerMixin:
    """``SessionDB.set_session_user_id``."""

    def set_session_user_id(self, session_id: str, user_id: Optional[str]) -> bool:
        """Attach (or clear) the owning account on a session row; True when a row was updated."""
        def _do(conn) -> int:
            cursor = conn.execute("UPDATE sessions SET user_id = ? WHERE id = ?", (user_id, session_id))
            rowcount = cursor.rowcount
            if rowcount is None or rowcount < 0:
                rowcount = conn.execute("SELECT changes()").fetchone()[0]
            return rowcount
        return self._execute_write(_do) > 0
