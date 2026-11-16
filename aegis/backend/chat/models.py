"""Chat event models for the Aegis backend."""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import Any


RunState = str


@dataclass(slots=True)
class ApprovalRequestState:
    approval_id: str
    command: str
    description: str
    choices: list[str] = field(default_factory=lambda: ["once", "session", "always", "deny"])
    source: str = "main"
    remote_interaction_id: str | None = None
    responder: Any = None
    allow_session: bool = True
    allow_permanent: bool = True


@dataclass(slots=True)
class ClarifyRequestState:
    clarify_id: str
    question: str
    choices: list[str] | None = None
    awaiting_text: bool = False
    multi_select: bool = False
    source: str = "main"
    remote_interaction_id: str | None = None
    responder: Any = None
    answer: str | None = None
    event: threading.Event = field(default_factory=threading.Event)


@dataclass(slots=True)
class DelegateForegroundState:
    child_session_id: str | None = None
    srcagent: str | None = None
    reason: str | None = None


@dataclass(slots=True)
class ChatEventEnvelope:
    type: str
    session_id: str
    server_event_id: str
    ts: float
    turn_id: str | None
    source: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        data = {
            "type": self.type,
            "session_id": self.session_id,
            "server_event_id": self.server_event_id,
            "ts": self.ts,
            "turn_id": self.turn_id,
            "source": self.source,
        }
        data.update(self.payload)
        return data
