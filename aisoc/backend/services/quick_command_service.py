"""Composer quick commands and server-side token expansion for AISOC chat.

Token grammar and expansion semantics are forked from
``aegis/backend/services/chat_quick_command_service.py`` so the two chat
frontends stay protocol-compatible. AISOC replaces Aegis' per-user database
sources with code seeds (``quick_command_seeds.SEED_COMMANDS``) plus an
optional operator extension file ``$HERMES_HOME/aisoc_quick_commands.json``.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import re
import threading
from typing import Any

from aisoc.backend.models import ChatQuickCommandResponse
from aisoc.backend.services.quick_command_seeds import SEED_COMMANDS


_SHORTCUT_TOKEN_PATTERN = re.compile(r"@\[(agent|prompt|instruct)_[^\]]+\]")
_AGENT_VARIABLE_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

USER_COMMANDS_FILENAME = "aisoc_quick_commands.json"


class MessageArgumentResolutionError(ValueError):
    """A message supplied malformed template arguments."""


def _hermes_home() -> Path:
    env_home = str(os.environ.get("HERMES_HOME") or "").strip()
    if env_home:
        return Path(env_home)
    try:
        from hermes_constants import get_hermes_home

        return Path(str(get_hermes_home()))
    except Exception:
        return Path.cwd()


class QuickCommandService:
    """Serve the composer shortcut list and expand ``@[type_name]`` tokens."""

    def __init__(self, user_commands_path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._explicit_user_commands_path = user_commands_path
        self._cached_commands: list[ChatQuickCommandResponse] | None = None
        self._cached_user_file_mtime: float | None = None

    def _user_commands_path(self) -> Path:
        if self._explicit_user_commands_path is not None:
            return self._explicit_user_commands_path
        return _hermes_home() / USER_COMMANDS_FILENAME

    def list_commands(self) -> list[ChatQuickCommandResponse]:
        with self._lock:
            user_path = self._user_commands_path()
            try:
                mtime: float | None = user_path.stat().st_mtime
            except OSError:
                mtime = None
            if self._cached_commands is not None and mtime == self._cached_user_file_mtime:
                return list(self._cached_commands)
            commands = self._build_commands(user_path)
            self._cached_commands = commands
            self._cached_user_file_mtime = mtime
            return list(commands)

    def invalidate(self) -> None:
        with self._lock:
            self._cached_commands = None
            self._cached_user_file_mtime = None

    def resolve_text(
        self,
        text: str,
        *,
        args: Mapping[str, Any] | None = None,
    ) -> str:
        resolved_args = self._normalize_args(args)
        commands_by_token = {
            self._token_for(command): command
            for command in self.list_commands()
        }

        def _replace(match: re.Match[str]) -> str:
            token = match.group(0)
            command = commands_by_token.get(token)
            if command is None:
                return token
            if command.type == "prompt":
                return command.content
            if command.type == "instruct":
                return f"{command.content}\n"
            return self._render_agent_command(command)

        # ``re.sub`` visits only original input matches, so replacement content
        # is deliberately never scanned again for nested shortcut tokens.
        resolved_text = _SHORTCUT_TOKEN_PATTERN.sub(_replace, str(text or ""))
        return _AGENT_VARIABLE_PATTERN.sub(
            lambda match: resolved_args.get(match.group(1), match.group(0)),
            resolved_text,
        )

    @staticmethod
    def _normalize_args(args: Mapping[str, Any] | None) -> dict[str, str]:
        if args is None:
            return {}
        if not isinstance(args, Mapping):
            raise MessageArgumentResolutionError("Message args must be a JSON object.")

        normalized: dict[str, str] = {}
        for key, value in args.items():
            if not isinstance(key, str):
                raise MessageArgumentResolutionError("Message args keys must be strings.")
            if isinstance(value, str):
                normalized[key] = value
                continue
            try:
                normalized[key] = json.dumps(
                    value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            except (TypeError, ValueError) as exc:
                raise MessageArgumentResolutionError(
                    f"Message arg {key!r} is not JSON serializable."
                ) from exc
        return normalized

    def _build_commands(self, user_path: Path) -> list[ChatQuickCommandResponse]:
        merged: dict[tuple[str, str], ChatQuickCommandResponse] = {}
        for raw in SEED_COMMANDS:
            command = self._parse_command(raw)
            if command is not None:
                merged[(command.type, command.name)] = command
        for raw in self._load_user_commands(user_path):
            command = self._parse_command(raw)
            if command is not None:
                # Operator file wins on (type, name) collisions with seeds.
                merged[(command.type, command.name)] = command
        commands = list(merged.values())
        commands.sort(key=lambda command: (command.type, command.name.casefold()))
        return commands

    @staticmethod
    def _parse_command(raw: object) -> ChatQuickCommandResponse | None:
        if not isinstance(raw, dict):
            return None
        try:
            return ChatQuickCommandResponse(
                type=str(raw.get("type") or ""),
                name=str(raw.get("name") or ""),
                desc=str(raw.get("desc") or ""),
                content=str(raw.get("content") or ""),
            )
        except Exception:
            return None

    @staticmethod
    def _load_user_commands(user_path: Path) -> list[object]:
        try:
            payload = json.loads(user_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if isinstance(payload, dict):
            payload = payload.get("commands")
        return payload if isinstance(payload, list) else []

    @staticmethod
    def _token_for(command: ChatQuickCommandResponse) -> str:
        return f"@[{command.type}_{command.name}]"

    @staticmethod
    def _render_agent_command(
        command: ChatQuickCommandResponse,
    ) -> str:
        return _AGENT_VARIABLE_PATTERN.sub(
            lambda match: command.name if match.group(1) in {"name", "agent_name"} else match.group(0),
            command.content,
        )
