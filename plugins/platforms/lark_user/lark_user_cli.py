"""Async ``lark-cli`` runner, always as the USER identity.

Message bodies go over stdin (``--markdown -`` / ``--text -`` / ``--content -``), never argv: a
long reply would hit ARG_MAX and argv is visible to every local process. The CLI writes one JSON
object per call; ``ok: false`` becomes a ``LarkCliError`` whose ``kind`` drives the poller's
reaction (``auth`` parks the adapter, ``rate_limit`` backs off, the rest retries).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Sequence

from hermes_platform.resolver import locate_command

logger = logging.getLogger(__name__)

# argv, stdin bytes -> (returncode, stdout, stderr); tests substitute a fake.
Runner = Callable[[Sequence[str], Optional[bytes], float], Awaitable[tuple[int, bytes, bytes]]]

_AUTH_HINTS = ("token", "login", "unauthor", "authenticat", "expired", "scope", "permission")
_RATE_HINTS = ("99991400", "rate limit", "frequency", "too many")


class LarkCliError(RuntimeError):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind  # auth | rate_limit | config | timeout | missing | api


def classify_error(error: dict[str, Any]) -> str:
    kind = str(error.get("type") or "").lower()
    text = " ".join(str(error.get(k) or "") for k in ("type", "subtype", "message", "code")).lower()
    if any(hint in text for hint in _RATE_HINTS):
        return "rate_limit"
    if kind in {"auth", "authentication", "authorization"} or any(hint in text for hint in _AUTH_HINTS):
        return "auth"
    if kind == "config":
        return "config"
    return "api"


async def _run_subprocess(argv: Sequence[str], stdin: Optional[bytes], timeout: float) -> tuple[int, bytes, bytes]:
    # cwd is a private temp dir: the CLI resolves relative media paths against it, never ours.
    with tempfile.TemporaryDirectory(prefix="lark_user_") as cwd:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout)
        except TimeoutError:
            proc.kill()
            with contextlib.suppress(TimeoutError):  # a grandchild holding the pipe must not hang us
                await asyncio.wait_for(proc.wait(), timeout=5)
            raise
    return int(proc.returncode or 0), stdout, stderr


@dataclass(frozen=True)
class SentMessage:
    message_id: str
    chat_id: str = ""


class LarkCli:
    def __init__(self, binary: str = "lark-cli", *, profile: str = "", timeout: float = 60.0,
                 runner: Optional[Runner] = None):
        self._binary = binary
        self._profile = profile
        self._timeout = timeout
        self._runner = runner or _run_subprocess

    def resolve_binary(self) -> Optional[str]:
        if os.path.dirname(self._binary):
            return self._binary if os.path.isfile(self._binary) else None
        found = locate_command(self._binary).command
        return found[0] if found else None

    async def call(self, args: Sequence[str], *, stdin: Optional[str] = None) -> dict[str, Any]:
        binary = self.resolve_binary()
        if not binary:
            raise LarkCliError("missing", f"{self._binary} not found on PATH")
        argv = [binary, *(["--profile", self._profile] if self._profile else []), *args]
        try:
            code, out, err = await self._runner(argv, stdin.encode() if stdin is not None else None, self._timeout)
        except TimeoutError:
            raise LarkCliError("timeout", f"lark-cli {args[0]} {args[1] if len(args) > 1 else ''} timed out") from None
        try:
            payload = json.loads(out.decode("utf-8", errors="replace") or "{}")
        except json.JSONDecodeError:
            logger.debug("lark_user: non-JSON lark-cli output (rc=%s): %r / %r", code, out[:500], err[:500])
            raise LarkCliError("api", f"lark-cli returned non-JSON output (rc={code})") from None
        if not payload.get("ok", code == 0):
            error = payload.get("error") or {}
            raise LarkCliError(classify_error(error), str(error.get("message") or f"lark-cli failed (rc={code})"))
        return payload.get("data") or {}

    # ── identity ────────────────────────────────────────────────────────────

    async def user_info(self) -> dict[str, Any]:
        """``{open_id, tenant_key, name}`` of the authorized user."""
        return await self.call(["api", "GET", "/open-apis/authen/v1/user_info", "--as", "user"])

    # ── reads ───────────────────────────────────────────────────────────────

    async def search(self, *, chat_type: str, start: str, at_me: bool = False, page_size: int = 50,
                     page_limit: int = 5) -> list[dict[str, Any]]:
        args = ["im", "+messages-search", "--as", "user", "--chat-type", chat_type, "--start", start,
                "--page-size", str(page_size), "--page-limit", str(page_limit), "--no-reactions"]
        if at_me:
            args.append("--is-at-me")
        data = await self.call(args)
        return list(data.get("messages") or [])

    # ── writes ──────────────────────────────────────────────────────────────

    @staticmethod
    def _body_args(fmt: str) -> list[str]:
        if fmt == "card":
            return ["--msg-type", "interactive", "--content", "-"]
        return ["--markdown", "-"] if fmt == "post" else ["--text", "-"]

    async def reply(self, message_id: str, body: str, *, fmt: str, in_thread: bool = False,
                    idempotency_key: str = "") -> SentMessage:
        args = ["im", "+messages-reply", "--as", "user", "--message-id", message_id, *self._body_args(fmt)]
        if in_thread:
            args.append("--reply-in-thread")
        if idempotency_key:
            args += ["--idempotency-key", idempotency_key[:50]]
        data = await self.call(args, stdin=body)
        return SentMessage(str(data.get("message_id") or ""), str(data.get("chat_id") or ""))

    async def send(self, body: str, *, fmt: str, chat_id: str = "", user_id: str = "",
                   idempotency_key: str = "") -> SentMessage:
        target = ["--chat-id", chat_id] if chat_id else ["--user-id", user_id]
        args = ["im", "+messages-send", "--as", "user", *target, *self._body_args(fmt)]
        if idempotency_key:
            args += ["--idempotency-key", idempotency_key[:50]]
        data = await self.call(args, stdin=body)
        return SentMessage(str(data.get("message_id") or ""), str(data.get("chat_id") or chat_id))

    async def chat_messages(self, chat_id: str, *, start: str, page_size: int) -> list[dict[str, Any]]:
        """Newest-first messages of a chat created after ``start``."""
        data = await self.call(["im", "+chat-messages-list", "--as", "user", "--chat-id", chat_id, "--start", start,
                                "--order", "desc", "--page-size", str(page_size), "--no-reactions"])
        return list(data.get("messages") or [])

    async def thread_messages(self, thread_id: str, *, page_size: int) -> list[dict[str, Any]]:
        """Newest-first messages of a topic."""
        data = await self.call(["im", "+threads-messages-list", "--as", "user", "--thread", thread_id,
                                "--order", "desc", "--page-size", str(page_size), "--no-reactions"])
        return list(data.get("messages") or [])
