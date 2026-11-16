"""Process-local broker for Hermes A2A human interactions.

The A2A protocol deliberately keeps an interaction on the current task in
``working`` state.  This module is the small seam between that wire protocol
and the existing blocking approval/clarify primitives: callers register a
request with a resolver, publish the returned metadata, and the authenticated
HTTP endpoint resolves it later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import threading
import time
from typing import Any, Callable


INTERACTION_EXTENSION_URI = "https://hermes.dev/extensions/interaction/v1"
INTERACTION_RESPONSE_SUFFIX = "/hermes/interaction/respond"
STEER_REQUEST_SUFFIX = "/hermes/steer"


class InteractionError(Exception):
    """An interaction response could not be accepted."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = int(status_code)
        self.detail = detail


@dataclass(slots=True)
class InteractionRecord:
    interaction_id: str
    task_id: str
    context_id: str
    session_key: str
    kind: str
    payload: dict[str, Any]
    resolver: Callable[[str], Any]
    created_at: float = field(default_factory=time.monotonic)
    state: str = "pending"
    resolved_value: str | None = None
    terminal_reason: str | None = None


class InteractionRegistry:
    """Thread-safe interaction registry with explicit terminal states.

    The registry retains terminal records for a bounded time so duplicate
    clicks can return a useful conflict instead of looking like an unknown
    interaction.  Active records are cancelled during executor cleanup;
    their underlying approval/clarify wait is released by the existing
    primitives owned by the executor.
    """

    _MAX_RECORDS = 4096

    def __init__(self):
        self._lock = threading.RLock()
        self._records: dict[str, InteractionRecord] = {}

    def register(
        self,
        *,
        interaction_id: str,
        task_id: str,
        context_id: str,
        session_key: str,
        kind: str,
        payload: dict[str, Any],
        resolver: Callable[[str], Any],
    ) -> InteractionRecord:
        normalized_id = str(interaction_id or "").strip()
        if not normalized_id:
            raise ValueError("interaction_id is required")
        if kind not in {"approval", "clarify"}:
            raise ValueError(f"unsupported interaction kind: {kind}")
        record = InteractionRecord(
            interaction_id=normalized_id,
            task_id=str(task_id or ""),
            context_id=str(context_id or ""),
            session_key=str(session_key or ""),
            kind=kind,
            payload=dict(payload),
            resolver=resolver,
        )
        with self._lock:
            old = self._records.get(normalized_id)
            if old is not None and old.state == "pending":
                raise ValueError(f"interaction already exists: {normalized_id}")
            self._records[normalized_id] = record
            self._trim_locked()
        return record

    def metadata(self, interaction_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._records.get(str(interaction_id or ""))
            if record is None:
                return None
            data = dict(record.payload)
            data.update(
                {
                    "kind": f"{record.kind}_request",
                    "interaction_id": record.interaction_id,
                    "task_id": record.task_id,
                    "context_id": record.context_id,
                }
            )
            return data

    def resolve(self, payload: dict[str, Any]) -> InteractionRecord:
        interaction_id = str(payload.get("interaction_id") or "").strip()
        task_id = str(payload.get("task_id") or "").strip()
        context_id = str(payload.get("context_id") or "").strip()
        kind = str(payload.get("kind") or "").strip().lower()
        if not interaction_id or not task_id or not context_id or not kind:
            raise InteractionError(422, "task_id, context_id, interaction_id and kind are required")

        with self._lock:
            record = self._records.get(interaction_id)
            if record is None:
                raise InteractionError(404, "interaction not found")
            if record.state != "pending":
                raise InteractionError(409, f"interaction is {record.state}")
            if (
                record.task_id != task_id
                or record.context_id != context_id
                or record.kind != kind
            ):
                raise InteractionError(409, "task, context, and interaction do not match")
            if kind == "approval":
                value = str(payload.get("choice") or "").strip().lower()
                if value not in {"once", "session", "always", "deny"}:
                    raise InteractionError(422, "approval choice must be once, session, always, or deny")
            else:
                answer = payload.get("answer")
                if isinstance(answer, list):
                    if not answer or any(not str(item).strip() for item in answer):
                        raise InteractionError(422, "clarify answer must not be empty")
                    value = json.dumps(
                        [str(item) for item in answer], ensure_ascii=False, separators=(",", ":")
                    )
                else:
                    value = str(answer or "").strip()
                    if not value:
                        raise InteractionError(422, "clarify answer must not be empty")
            record.state = "resolving"

        try:
            resolved = record.resolver(value)
        except Exception as exc:
            with self._lock:
                record.state = "pending"
            raise InteractionError(409, f"interaction could not be resolved: {exc}") from exc

        accepted = bool(resolved)
        if isinstance(resolved, int):
            accepted = resolved > 0
        with self._lock:
            if accepted:
                record.state = "resolved"
                record.resolved_value = value
                record.terminal_reason = "accepted"
            else:
                record.state = "expired"
                record.terminal_reason = "underlying wait is no longer pending"
        if not accepted:
            raise InteractionError(409, "interaction expired or is no longer pending")
        return record

    def cancel_session(self, session_key: str, reason: str = "cancelled") -> int:
        cancelled = 0
        with self._lock:
            for record in self._records.values():
                if record.session_key == session_key and record.state in {"pending", "resolving"}:
                    record.state = "cancelled"
                    record.terminal_reason = reason
                    cancelled += 1
        return cancelled

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def _trim_locked(self) -> None:
        if len(self._records) <= self._MAX_RECORDS:
            return
        terminal = [
            (record.created_at, interaction_id)
            for interaction_id, record in self._records.items()
            if record.state != "pending"
        ]
        terminal.sort()
        for _created_at, interaction_id in terminal[: max(0, len(self._records) - self._MAX_RECORDS)]:
            self._records.pop(interaction_id, None)


def interaction_extension(response_path: str, steer_path: str | None = None) -> dict[str, Any]:
    """Return the non-required AgentCard extension declaration.

    ``steer_path`` advertises mid-turn steering; callers that do not see it
    must keep queueing follow-up input as a new turn.
    """
    params: dict[str, Any] = {"response_path": response_path}
    if steer_path:
        params["steer_path"] = steer_path
    return {
        "uri": INTERACTION_EXTENSION_URI,
        "required": False,
        "params": params,
    }
