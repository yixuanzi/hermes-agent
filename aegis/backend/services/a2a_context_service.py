"""Cached, authenticated API view of the existing A2A registry context."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from typing import Any

from aegis.backend.models import (
    A2AContextAgentResponse,
    A2AContextResponse,
    A2AGlobalRoutingRuleResponse,
)


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class A2AContextService:
    """Maintain one process-lifetime snapshot of the A2A discovery result.

    The source of truth remains :mod:`tools.a2a_delegate_tool`: calling its
    JSON output keeps the REST view aligned with the ``/a2a`` slash command's
    registry parsing, remote agent-card probing, and capability merge rules.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snapshot: A2AContextResponse | None = None

    def get_context(self) -> A2AContextResponse:
        """Return a cached snapshot, populating it on the first request."""
        with self._lock:
            if self._snapshot is not None:
                return self._snapshot.model_copy(deep=True)
            return self._refresh_locked()

    def refresh_context(self) -> A2AContextResponse:
        """Refresh the snapshot and preserve the last successful result on error."""
        with self._lock:
            return self._refresh_locked()

    def _refresh_locked(self) -> A2AContextResponse:
        context, error = self._fetch_context()
        if context is not None:
            self._snapshot = context
            return context.model_copy(deep=True)

        if self._snapshot is not None:
            return self._snapshot.model_copy(
                update={"stale": True, "refresh_error": error or "Unable to refresh A2A context."},
                deep=True,
            )

        return A2AContextResponse(
            stale=True,
            refresh_error=error or "Unable to refresh A2A context.",
        )

    def _fetch_context(self) -> tuple[A2AContextResponse | None, str | None]:
        try:
            # Import on refresh so the existing A2A tool remains the single
            # owner of registry behavior and its process-level registry state.
            from tools import a2a_delegate_tool

            raw_payload = a2a_delegate_tool.a2a_list("json")
            payload = json.loads(raw_payload)
        except Exception as exc:
            return None, f"Unable to read A2A context: {exc}"

        if not isinstance(payload, dict):
            return None, "Unable to read A2A context: invalid registry response."
        if not payload.get("success"):
            return None, str(payload.get("error") or "Unable to refresh A2A context.")

        try:
            return self._parse_context(payload), None
        except (TypeError, ValueError) as exc:
            return None, f"Unable to read A2A context: {exc}"

    @staticmethod
    def _parse_context(payload: dict[str, Any]) -> A2AContextResponse:
        raw_agents = payload.get("agents", [])
        raw_routing = payload.get("global_routing", [])
        if not isinstance(raw_agents, list) or not isinstance(raw_routing, list):
            raise ValueError("invalid registry response shape")

        agents = [
            A2AContextAgentResponse(
                name=str(entry.get("name") or "Unnamed agent"),
                url=A2AContextService._optional_text(entry.get("url")),
                status=A2AContextService._optional_text(entry.get("status")),
                available=bool(entry.get("available")),
                description=A2AContextService._optional_text(entry.get("description")),
                capabilities=A2AContextService._string_list(entry.get("capabilities")),
                error=A2AContextService._optional_text(entry.get("error")),
            )
            for entry in raw_agents
            if isinstance(entry, dict)
        ]
        routing = [
            A2AGlobalRoutingRuleResponse(
                id=str(entry.get("id") or ""),
                name=str(entry.get("name") or ""),
                policy=str(entry.get("policy") or ""),
                status=str(entry.get("status") or ""),
            )
            for entry in raw_routing
            if isinstance(entry, dict)
        ]
        return A2AContextResponse(
            agents=agents,
            global_routing=routing,
            refreshed_at=_utc_timestamp(),
        )

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]
