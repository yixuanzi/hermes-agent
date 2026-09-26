"""Recent group / topic history for Feishu cold-start turns.

When the Feishu adapter opens a *new* session for a group message it can hand
the agent the conversation it is being pulled into: the chat's recent
chat-level messages as a ``<group_messages>`` block and, when the trigger sits
inside a topic, that topic's recent messages as a ``<thread_messages>`` block.
The adapter stores the rendered text on ``MessageEvent.metadata[METADATA_KEY]``
and the gateway places it directly under the ``<source>`` header.

This module owns everything below the adapter's gate:

* :class:`GroupHistorySettings` — the knobs (all ``FEISHU_GROUP_HISTORY_*``).
* :class:`HistoryMessage` — one normalised message, whatever fetched it.
* Two fetch backends behind the same contract:
  :class:`ApiHistorySource` (``im/v1/messages`` through the lark SDK client)
  and :class:`LarkCliHistorySource` (``lark-cli im +chat-messages-list`` /
  ``+threads-messages-list``, whose ``content`` is already rendered text and,
  unlike the raw API, carries interactive-card bodies).
* :func:`render_history_block` / :func:`build_group_history_block` — the
  shared selection, labelling and wrapping so both backends produce
  byte-identical block shapes.

Everything degrades to ``""`` on failure; history must never block a turn.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants / settings
# ---------------------------------------------------------------------------

PAGE_SIZE_MAX = 50                 # im/v1/messages page_size ceiling (API and CLI alike)
DEFAULT_LIMIT = 20
DEFAULT_HOURS = 24.0
DEFAULT_THREAD_LIMIT = 20
DEFAULT_MSG_MAX_CHARS = 1000       # 0 = no per-message truncation
SOURCE_API = "api"
SOURCE_CLI = "cli"
DEFAULT_CLI_BIN = "lark-cli"
CLI_TIMEOUT_SECONDS = 30.0
METADATA_KEY = "group_history_block"   # MessageEvent.metadata key the gateway reads

_CLI_SOURCE_ALIASES = {"lark-cli", "lark_cli", "larkcli", "cli", "feishu-cli", "feishu_cli", "feishucli"}


def normalize_history_source(value: Any) -> str:
    """Map ``FEISHU_GROUP_HISTORY_SOURCE`` spellings onto ``api`` / ``cli``.

    ``cli`` is the canonical value; ``lark-cli`` / ``feishu-cli`` and their
    underscore forms are accepted as aliases.

    Unknown values fall back to ``api`` (with a warning) rather than silently
    switching to a subprocess-backed fetch the operator did not ask for.
    """
    raw = str(value or "").strip().lower()
    if not raw or raw == SOURCE_API:
        return SOURCE_API
    if raw in _CLI_SOURCE_ALIASES:
        return SOURCE_CLI
    logger.warning("[Feishu] Unknown group history source %r; using %s", value, SOURCE_API)
    return SOURCE_API


@dataclass(frozen=True)
class GroupHistorySettings:
    enabled: bool = False
    limit: int = DEFAULT_LIMIT
    hours: float = DEFAULT_HOURS
    thread_limit: int = DEFAULT_THREAD_LIMIT
    msg_max_chars: int = DEFAULT_MSG_MAX_CHARS
    source: str = SOURCE_API
    cli_bin: str = DEFAULT_CLI_BIN


# ---------------------------------------------------------------------------
# Normalised message + fetch contract
# ---------------------------------------------------------------------------


@dataclass
class HistoryMessage:
    """One message as the renderer needs it, independent of the backend."""

    message_id: str
    text: str                  # rendered body, not yet neutralised / truncated
    sender_id: str = ""
    sender_type: str = ""      # "user" | "app" | ""
    sender_name: str = ""      # display name when the backend already knows it
    stamp: str = ""            # "MM-DD HH:MM" or ""
    is_reply: bool = False     # has a root/parent message
    in_thread: bool = False    # reply that sits inside a topic
    deleted: bool = False


class HistorySource(Protocol):
    """A backend that lists messages newest-first, or ``None`` on failure."""

    name: str

    async def list_chat(
        self, chat_id: str, *, page_size: int, since_epoch: float
    ) -> Optional[List[HistoryMessage]]: ...

    async def list_thread(
        self, thread_id: str, *, page_size: int
    ) -> Optional[List[HistoryMessage]]: ...


def _clamp_page_size(page_size: int) -> int:
    return max(1, min(int(page_size), PAGE_SIZE_MAX))


def _timezone() -> Any:
    try:
        from hermes_time import get_timezone

        return get_timezone()
    except Exception:  # noqa: BLE001 — a tz problem must not cost the block
        return None


def format_stamp_from_millis(create_time: Any, tz: Any = None) -> str:
    """Render a Feishu ``create_time`` (ms since epoch) as ``MM-DD HH:MM``."""
    try:
        millis = int(str(create_time).strip())
    except (TypeError, ValueError):
        return ""
    if millis <= 0:
        return ""
    try:
        return datetime.fromtimestamp(millis / 1000, tz).strftime("%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return ""


_CLI_STAMP_RE = re.compile(r"^\d{4}-(\d{2}-\d{2})[ T](\d{2}:\d{2})")


def format_stamp_from_cli(create_time: Any, tz: Any = None) -> str:
    """lark-cli renders ``create_time`` as ``YYYY-MM-DD HH:MM``; keep ``MM-DD HH:MM``.

    Also accepts an ISO-8601 ``T`` separator, and falls back to the
    millisecond parser for raw values.
    """
    raw = str(create_time or "").strip()
    match = _CLI_STAMP_RE.match(raw)
    if match:
        return f"{match.group(1)} {match.group(2)}"
    return format_stamp_from_millis(raw, tz)


# ---------------------------------------------------------------------------
# Backend 1: lark SDK client (im/v1/messages)
# ---------------------------------------------------------------------------


class ApiHistorySource:
    """Fetch through the adapter's lark SDK client on its blocking executor."""

    name = SOURCE_API

    def __init__(
        self,
        *,
        client: Any,
        run_blocking: Callable[..., Awaitable[Any]],
        extract_text: Callable[..., Optional[str]],
        tz: Any = None,
    ) -> None:
        self._client = client
        self._run_blocking = run_blocking
        self._extract_text = extract_text
        self._tz = tz

    async def list_chat(
        self, chat_id: str, *, page_size: int, since_epoch: float
    ) -> Optional[List[HistoryMessage]]:
        # ``start_time`` is unix seconds; item ``create_time`` comes back in ms.
        return await self._list("chat", chat_id, page_size, start_time=str(int(since_epoch)))

    async def list_thread(self, thread_id: str, *, page_size: int) -> Optional[List[HistoryMessage]]:
        return await self._list("thread", thread_id, page_size)

    async def _list(
        self,
        container_id_type: str,
        container_id: str,
        page_size: int,
        *,
        start_time: Optional[str] = None,
    ) -> Optional[List[HistoryMessage]]:
        from lark_oapi.api.im.v1 import ListMessageRequest

        builder = (
            ListMessageRequest.builder()
            .container_id_type(container_id_type)
            .container_id(container_id)
            .sort_type("ByCreateTimeDesc")
            .page_size(_clamp_page_size(page_size))
        )
        if start_time is not None:
            builder = builder.start_time(start_time)
        response = await self._run_blocking(self._client.im.v1.message.list, builder.build())
        if not response or getattr(response, "success", lambda: False)() is False:
            logger.warning(
                "[Feishu] Failed to list %s history for %s: [%s] %s",
                container_id_type,
                container_id,
                getattr(response, "code", "unknown"),
                getattr(response, "msg", "message list failed"),
            )
            return None
        items = getattr(getattr(response, "data", None), "items", None) or []
        return [self._to_message(item) for item in items]

    def _to_message(self, item: Any) -> HistoryMessage:
        body = getattr(item, "body", None)
        text = self._extract_text(
            msg_type=str(getattr(item, "msg_type", "") or ""),
            raw_content=str(getattr(body, "content", "") or ""),
            mentions=getattr(item, "mentions", None),
        )
        sender = getattr(item, "sender", None)
        root_id = getattr(item, "root_id", None)
        return HistoryMessage(
            message_id=str(getattr(item, "message_id", "") or ""),
            text=str(text or ""),
            sender_id=str(getattr(sender, "id", "") or ""),
            sender_type=str(getattr(sender, "sender_type", "") or ""),
            sender_name=str(getattr(sender, "sender_name", "") or ""),
            stamp=format_stamp_from_millis(getattr(item, "create_time", None), self._tz),
            is_reply=bool(root_id),
            in_thread=bool(root_id and getattr(item, "thread_id", None)),
            deleted=bool(getattr(item, "deleted", False)),
        )


# ---------------------------------------------------------------------------
# Backend 2: lark-cli (im +chat-messages-list / +threads-messages-list)
# ---------------------------------------------------------------------------

CommandRunner = Callable[[Sequence[str]], Awaitable[Tuple[int, bytes, bytes]]]


async def _run_subprocess(argv: Sequence[str], *, timeout: float) -> Tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return int(proc.returncode or 0), stdout, stderr


class LarkCliHistorySource:
    """Fetch through ``lark-cli`` as the bot identity.

    The CLI returns ``content`` already rendered to text (mentions substituted,
    interactive cards flattened to ``<card title=...>…</card>``), a resolved
    ``sender.name`` and a ``YYYY-MM-DD HH:MM`` ``create_time``.  Its chat
    listing auto-expands ``thread_replies``; those are ignored here so the
    ``<group_messages>`` block stays chat-level like the API backend, and the
    topic is fetched separately through ``+threads-messages-list``.
    """

    name = SOURCE_CLI

    def __init__(
        self,
        *,
        binary: str = DEFAULT_CLI_BIN,
        timeout: float = CLI_TIMEOUT_SECONDS,
        runner: Optional[CommandRunner] = None,
        tz: Any = None,
    ) -> None:
        self._binary = (binary or DEFAULT_CLI_BIN).strip() or DEFAULT_CLI_BIN
        self._timeout = timeout
        self._runner = runner
        self._tz = tz

    async def list_chat(
        self, chat_id: str, *, page_size: int, since_epoch: float
    ) -> Optional[List[HistoryMessage]]:
        start_iso = datetime.fromtimestamp(since_epoch).astimezone().isoformat(timespec="seconds")
        argv = [
            "im", "+chat-messages-list",
            "--chat-id", chat_id,
            "--start", start_iso,
        ]
        return await self._list(argv, label=f"chat {chat_id}", page_size=page_size)

    async def list_thread(self, thread_id: str, *, page_size: int) -> Optional[List[HistoryMessage]]:
        argv = ["im", "+threads-messages-list", "--thread", thread_id]
        return await self._list(argv, label=f"thread {thread_id}", page_size=page_size)

    def _resolve_binary(self) -> Optional[str]:
        return shutil.which(self._binary) or (self._binary if "/" in self._binary else None)

    async def _list(self, argv: List[str], *, label: str, page_size: int) -> Optional[List[HistoryMessage]]:
        binary = self._resolve_binary()
        if not binary:
            logger.warning("[Feishu] lark-cli binary %r not found; no group history for %s", self._binary, label)
            return None
        full_argv = [
            binary, *argv,
            "--as", "bot",
            "--order", "desc",
            "--page-size", str(_clamp_page_size(page_size)),
            "--no-reactions",
            "--format", "json",
        ]
        try:
            if self._runner is not None:
                code, stdout, stderr = await self._runner(full_argv)
            else:
                code, stdout, stderr = await _run_subprocess(full_argv, timeout=self._timeout)
        except asyncio.TimeoutError:
            logger.warning("[Feishu] lark-cli timed out after %.0fs listing %s", self._timeout, label)
            return None
        except Exception:  # noqa: BLE001 — subprocess plumbing must not cost the turn
            logger.warning("[Feishu] lark-cli failed to start for %s", label, exc_info=True)
            return None
        if code != 0:
            logger.warning(
                "[Feishu] lark-cli exited %s listing %s: %s",
                code,
                label,
                (stderr or stdout or b"")[:300].decode("utf-8", "replace").strip(),
            )
            return None
        try:
            payload = json.loads(stdout.decode("utf-8", "replace") or "{}")
        except ValueError:
            logger.warning("[Feishu] lark-cli returned non-JSON output listing %s", label)
            return None
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            logger.warning(
                "[Feishu] lark-cli reported failure listing %s: %s",
                label,
                str(payload)[:300] if payload else "empty payload",
            )
            return None
        data = payload.get("data") or {}
        messages = data.get("messages") if isinstance(data, dict) else None
        if not isinstance(messages, list):
            return []
        return [self._to_message(m) for m in messages if isinstance(m, dict)]

    def _to_message(self, m: Dict[str, Any]) -> HistoryMessage:
        sender = m.get("sender") or {}
        if not isinstance(sender, dict):
            sender = {}
        root_id = m.get("root_id") or m.get("parent_id")
        content = m.get("content")
        return HistoryMessage(
            message_id=str(m.get("message_id") or ""),
            text=content if isinstance(content, str) else ("" if content is None else str(content)),
            sender_id=str(sender.get("id") or ""),
            sender_type=str(sender.get("sender_type") or ""),
            sender_name=str(sender.get("name") or ""),
            stamp=format_stamp_from_cli(m.get("create_time"), self._tz),
            is_reply=bool(root_id),
            in_thread=bool(root_id and m.get("thread_id")),
            deleted=bool(m.get("deleted", False)),
        )


# ---------------------------------------------------------------------------
# Rendering (shared by both backends)
# ---------------------------------------------------------------------------


@dataclass
class HistoryRenderContext:
    """What the renderer needs from the adapter."""

    app_id: str
    resolve_sender_name: Callable[[str], Awaitable[Optional[str]]]
    msg_max_chars: int = DEFAULT_MSG_MAX_CHARS


async def render_history_block(
    messages: Sequence[HistoryMessage],
    *,
    exclude_ids: Set[str],
    limit: int,
    wrapper: str,
    tag_replies: bool,
    render: HistoryRenderContext,
) -> Tuple[str, Set[str]]:
    """Turn newest-first messages into a ``<wrapper>`` block.

    Returns ``(block, message_ids_included)``; ``("", set())`` when nothing
    usable remains.  ``tag_replies`` adds ``[reply]`` / ``[in-topic]``, which
    carry meaning in a chat listing and are noise inside one topic's listing.
    Names and bodies pass through ``neutralize_untrusted_inline_text`` so a
    crafted sender name or message cannot break out of its line.
    """
    from gateway.session import neutralize_untrusted_inline_text

    max_chars = max(0, int(render.msg_max_chars))
    selected: List[HistoryMessage] = []
    included: Set[str] = set()
    for msg in messages:
        if msg.deleted:
            continue
        if msg.message_id and msg.message_id in exclude_ids:
            continue
        if not (msg.text or "").strip():
            continue
        selected.append(msg)
        if msg.message_id:
            included.add(msg.message_id)
        if len(selected) >= limit:
            break
    if not selected:
        return "", set()
    selected.reverse()  # chronological order

    # Resolve human sender names the backend did not supply, once per id.
    names: Dict[str, str] = {}
    for msg in selected:
        if not msg.sender_id or msg.sender_id in names or msg.sender_type == "app":
            continue
        if msg.sender_name:
            names[msg.sender_id] = msg.sender_name
            continue
        try:
            resolved = await render.resolve_sender_name(msg.sender_id)
        except Exception:  # noqa: BLE001 — a name lookup must not cost the block
            resolved = None
        names[msg.sender_id] = resolved or msg.sender_id

    lines: List[str] = []
    for msg in selected:
        tags = ""
        if tag_replies and msg.is_reply:
            tags = "[in-topic] " if msg.in_thread else "[reply] "
        if msg.sender_type == "app" and msg.sender_id and msg.sender_id == render.app_id:
            who = "[assistant]"
        elif msg.sender_type == "app":
            who = f"[bot] {neutralize_untrusted_inline_text(msg.sender_name or 'bot')}"
        else:
            who = neutralize_untrusted_inline_text(names.get(msg.sender_id) or msg.sender_id or "unknown")
        safe_text = neutralize_untrusted_inline_text(msg.text, max_chars=max_chars)
        prefix = f"[{msg.stamp}] " if msg.stamp else ""
        lines.append(f"{prefix}{tags}{who}: {safe_text}")
    return f"<{wrapper}>\n" + "\n".join(lines) + f"\n</{wrapper}>", included


def history_line_count(block: str) -> int:
    """Number of message lines inside a rendered ``<...>`` history block."""
    return max(block.count("\n") - 1, 0) if block else 0


async def build_group_history_block(
    *,
    source: HistorySource,
    settings: GroupHistorySettings,
    chat_id: str,
    exclude_message_id: str,
    thread_id: Optional[str],
    render: HistoryRenderContext,
    is_thread_anchor: Callable[[Any], bool],
) -> str:
    """Render recent history for a cold-start group turn.

    Always: the chat's messages created within the last ``settings.hours``,
    newest ``settings.limit`` of them, as ``<group_messages>``.  The chat
    listing only yields chat-level posts, never replies inside topics.

    Additionally, when ``thread_id`` names a real ``omt_*`` topic and
    ``settings.thread_limit`` > 0: that topic's newest ``thread_limit``
    messages (no time window — the topic *is* the conversation) as
    ``<thread_messages>`` after the group block.  Messages already shown in
    the group block, and the trigger, are not repeated.
    """
    if not chat_id:
        return ""
    limit = max(1, int(settings.limit))
    hours = float(settings.hours)
    thread_limit = max(0, int(settings.thread_limit))
    try:
        messages = await source.list_chat(
            chat_id, page_size=limit + 1, since_epoch=time.time() - hours * 3600
        )
        if messages is None:
            return ""
        exclude = {str(exclude_message_id or "")}
        group_block, shown_ids = await render_history_block(
            messages, exclude_ids=exclude, limit=limit, wrapper="group_messages",
            tag_replies=True, render=render,
        )

        thread_block = ""
        thread_count = 0
        fetch_thread = bool(thread_id and thread_limit > 0 and not is_thread_anchor(str(thread_id)))
        if fetch_thread:
            thread_messages = await source.list_thread(str(thread_id), page_size=thread_limit + 1)
            if thread_messages:
                thread_count = len(thread_messages)
                thread_block, _ = await render_history_block(
                    thread_messages, exclude_ids=exclude | shown_ids, limit=thread_limit,
                    wrapper="thread_messages", tag_replies=False, render=render,
                )

        logger.info(
            "[Feishu] Group history for %s via %s: %d chat item(s) within %.1fh -> %d line(s)%s",
            chat_id,
            getattr(source, "name", "?"),
            len(messages),
            hours,
            history_line_count(group_block),
            (
                f"; topic {thread_id}: {thread_count} item(s) -> {history_line_count(thread_block)} line(s)"
                if fetch_thread
                else ""
            ),
        )
        return "\n\n".join(block for block in (group_block, thread_block) if block)
    except Exception:  # noqa: BLE001 — history must never block a turn
        logger.warning("[Feishu] Failed to build group history for %s", chat_id, exc_info=True)
        return ""


def default_timezone() -> Any:
    """Process timezone for stamps (``hermes_time.get_timezone``), or ``None``."""
    return _timezone()
