"""Message conversion helpers for the WORKAGENT A2A module."""

from __future__ import annotations

from collections.abc import Iterable

from a2a.types import Message, Part, Role


def a2a_to_text(message: Message) -> str:
    """Collapse an A2A proto message into plain text for Hermes."""
    return "\n".join(part.text for part in message.parts if part.HasField("text")).strip()


def text_to_message(
    text: str,
    *,
    role: Role = Role.ROLE_AGENT,
    context_id: str = "",
    task_id: str = "",
) -> Message:
    """Convert plain text into a text-only A2A proto message."""
    return Message(
        role=role,
        context_id=context_id,
        task_id=task_id,
        parts=[Part(text=text)],
    )


def history_to_a2a(
    history: Iterable[dict[str, str]],
    *,
    context_id: str,
    task_id: str,
) -> list[Message]:
    """Convert OpenAI-style history into A2A messages."""
    result: list[Message] = []
    for item in history:
        role = Role.ROLE_USER if item.get("role") == "user" else Role.ROLE_AGENT
        content = item.get("content") or ""
        result.append(
            text_to_message(
                content,
                role=role,
                context_id=context_id,
                task_id=task_id,
            )
        )
    return result


def role_to_history_role(role: Role) -> str:
    """Convert an A2A role enum into Hermes/OpenAI history role text."""
    if role == Role.ROLE_USER:
        return "user"
    return "assistant"

