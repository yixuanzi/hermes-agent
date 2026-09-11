"""CardKit-backed card output for the Feishu adapter.

Historically every Hermes reply on Feishu landed as one or more ``text`` /
``post`` messages, and tool-progress chrome landed as *separate* editable
bubbles above (or below) that content.  A single agent turn therefore
scattered across three or four chat messages, and a long turn interleaved
them so the transcript read out of order.

This module implements the three-element card described in
``hermes-feishu-card-output.md``:

  ① Header title       — ``header.title``, one line naming the speaker.
  ② Execution trace    — a ``collapsible_panel`` holding a single ``markdown``
                         element; expanded while the turn runs, collapsed when
                         it finishes.  Every tool-progress / thinking line the
                         gateway would have sent as its own bubble is folded in
                         here instead.
  ③ Rich-text body     — a ``markdown`` element streamed through the CardKit
                         ``content`` endpoint, which gives the native
                         typewriter animation when each update is a prefix
                         superset of the previous one.

Card lifecycle
--------------
``POST /cardkit/v1/cards`` creates the card *entity* (JSON 2.0, so the
``markdown`` element and ``streaming_mode`` are available — the plain
``im/v1/messages`` send path parses JSON 1.0 only and rejects both).  The
entity is then *delivered* into the chat as ``msg_type=interactive`` with
``content={"type": "card", "data": {"card_id": ...}}``.  After that the card
is mutated in place:

  * trace area → ``PUT /cards/:id/elements/:element_id``  (full element replace)
  * body area  → ``PUT /cards/:id/elements/:element_id/content``  (streaming)
  * finalize   → ``PATCH /cards/:id/elements/:element_id`` (collapse the panel)
                 + ``PATCH /cards/:id/settings`` (streaming off, final summary)

Every mutating call needs a strictly increasing ``sequence`` per card, which
:class:`FeishuCardSession` owns.

Speaker boundaries
------------------
One card belongs to exactly one speaker.  The manager tracks the *owner* of the
live card; when output arrives from a different owner it seals the live card and
opens a new one.  That is what gives each ``a2a_delegate`` remote agent its own
card, and what makes the main agent open a fresh card when control returns to
it.

Degradation
-----------
Nothing here is allowed to cost the user their reply.  Any CardKit failure
marks the route as card-unavailable for a cooldown window and returns ``None``
from the entry points, which tells the adapter to fall back to the legacy
``text`` / ``post`` path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("hermes.feishu.cardkit")


# ---------------------------------------------------------------------------
# Lazy SDK binding
# ---------------------------------------------------------------------------
#
# ``lark_oapi`` is slow to import and is already deferred to connect time by
# the adapter (see ``_load_lark_oapi``).  The CardKit models live in their own
# subpackage, so bind them separately and treat their absence as "cards are not
# available" rather than as a hard error: an older lark-oapi still serves the
# legacy text/post path perfectly well.

CARDKIT_AVAILABLE = False
_cardkit_import_lock = threading.Lock()

CreateCardRequest = None  # type: ignore[assignment]
CreateCardRequestBody = None  # type: ignore[assignment]
ContentCardElementRequest = None  # type: ignore[assignment]
ContentCardElementRequestBody = None  # type: ignore[assignment]
UpdateCardElementRequest = None  # type: ignore[assignment]
UpdateCardElementRequestBody = None  # type: ignore[assignment]
PatchCardElementRequest = None  # type: ignore[assignment]
PatchCardElementRequestBody = None  # type: ignore[assignment]
SettingsCardRequest = None  # type: ignore[assignment]
SettingsCardRequestBody = None  # type: ignore[assignment]


def load_cardkit() -> bool:
    """Import and bind the CardKit models.  Idempotent and thread-safe."""
    global CARDKIT_AVAILABLE
    if CARDKIT_AVAILABLE:
        return True
    with _cardkit_import_lock:
        if CARDKIT_AVAILABLE:
            return True
        try:
            from lark_oapi.api.cardkit.v1 import (
                ContentCardElementRequest as _ContentReq,
                ContentCardElementRequestBody as _ContentBody,
                CreateCardRequest as _CreateReq,
                CreateCardRequestBody as _CreateBody,
                PatchCardElementRequest as _PatchReq,
                PatchCardElementRequestBody as _PatchBody,
                SettingsCardRequest as _SettingsReq,
                SettingsCardRequestBody as _SettingsBody,
                UpdateCardElementRequest as _UpdateReq,
                UpdateCardElementRequestBody as _UpdateBody,
            )
        except ImportError:
            logger.info("[Feishu] lark-oapi has no cardkit models; card output disabled")
            return False

        globals().update({
            "CreateCardRequest": _CreateReq,
            "CreateCardRequestBody": _CreateBody,
            "ContentCardElementRequest": _ContentReq,
            "ContentCardElementRequestBody": _ContentBody,
            "UpdateCardElementRequest": _UpdateReq,
            "UpdateCardElementRequestBody": _UpdateBody,
            "PatchCardElementRequest": _PatchReq,
            "PatchCardElementRequestBody": _PatchBody,
            "SettingsCardRequest": _SettingsReq,
            "SettingsCardRequestBody": _SettingsBody,
            "CARDKIT_AVAILABLE": True,
        })
        return True


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Stable element ids inside every Hermes card.  The card is created with all
#: three present, so no element ever has to be inserted at runtime.
PANEL_ELEMENT_ID = "hermes_trace_panel"
TRACE_ELEMENT_ID = "hermes_trace"
BODY_ELEMENT_ID = "hermes_body"

#: Synthetic message-id prefix.  ``send()`` hands these back to the gateway in
#: place of a real Feishu message id: a card block is not a message, and the
#: gateway must not try to edit/delete it through the ``im`` API.
BLOCK_ID_PREFIX = "hermes-card:"

#: Feishu rejects a card whose payload exceeds 30 KB (error 200860).  Budget
#: in *bytes*, not characters: CJK text costs three bytes per character, so a
#: character-based cap would let a long Chinese answer overflow the card long
#: before it looked close to the limit.  Body rolls onto a continuation card
#: when it fills up; the trace drops its oldest steps.
MAX_BODY_BYTES = 20000
MAX_TRACE_BYTES = 4000

#: The streaming endpoint accepts 50 calls/second per card, so the only reason
#: to coalesce is to avoid burning quota on updates nobody can read at that
#: speed — the client animates between them anyway.
DEFAULT_FLUSH_INTERVAL = 0.4

#: Client-side typewriter pacing (``config.streaming_config``).  ``fast``
#: prints as text arrives rather than pacing it out to look deliberate.
STREAMING_CONFIG: Dict[str, Any] = {
    "print_frequency_ms": {"default": 30, "android": 25, "ios": 40, "pc": 50},
    "print_step": {"default": 2, "android": 3, "ios": 4, "pc": 5},
    "print_strategy": "fast",
}

#: Feishu closes streaming mode by itself after a quiet stretch, and rejects a
#: streaming update while the user is mid-interaction with the card.  Both are
#: recoverable, so they must not end the turn's output.
_STREAMING_CLOSED_CODES = frozenset({200850, 300309})
_TRANSIENT_UPDATE_CODES = frozenset({200810, 300120})

#: How many times one area re-attempts a failed render before giving up.
_MAX_RENDER_RETRIES = 3

#: How long a route stays on the legacy path after a CardKit failure.
FAILURE_COOLDOWN_SECONDS = 300.0

_TRUNCATED_NOTICE = "\n\n_…（内容过长，已截断）_"
_TRACE_TRUNCATED_NOTICE = "_…（较早步骤已省略）_\n"


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CardCopy:
    """User-visible strings, overridable from the environment."""

    main_title: str = "🤖 Hermes"
    delegate_title: str = "🛰️ 委派 · {agent}"
    delegate_fallback_agent: str = "远程 Agent"
    trace_title: str = "🔧 执行过程"
    trace_title_done: str = "🔧 执行过程 · {steps} 步"
    trace_title_empty: str = "🔧 执行过程 · 无工具调用"
    trace_placeholder: str = "_准备中…_"
    body_placeholder: str = "_生成中…_"
    summary_running: str = "生成中…"
    summary_done: str = "✅ 已完成"
    summary_failed: str = "⚠️ 未完成"
    continuation_suffix: str = "（续）"
    template_running: str = "blue"


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

@dataclass
class _Block:
    """One editable unit of card text.

    The gateway streams a reply by sending a message and then editing it; a
    segment break produces a *new* message.  Each of those becomes a block, so
    an edit rewrites exactly the text it owns and the rendered area is simply
    its blocks joined back together.
    """

    block_id: str
    kind: str  # "trace" | "body"
    text: str = ""


@dataclass
class FeishuCardSession:
    """One delivered card and the text that currently renders inside it."""

    route_key: str
    owner: str
    title: str
    chat_id: str
    metadata: Optional[Dict[str, Any]] = None
    reply_to: Optional[str] = None

    card_id: str = ""
    message_id: str = ""
    sequence: int = 0
    blocks: List[_Block] = field(default_factory=list)
    trace_steps: int = 0
    closed: bool = False

    # Rendering bookkeeping
    rendered_trace: str = ""
    rendered_body: str = ""
    trace_dirty: bool = False
    body_dirty: bool = False
    trace_retries: int = 0
    body_retries: int = 0
    panel_collapsed: bool = False
    flush_task: Optional[asyncio.Task] = None
    last_flush_ts: float = 0.0
    lock: Optional[asyncio.Lock] = None

    def next_sequence(self) -> int:
        self.sequence += 1
        return self.sequence

    # -- block helpers ---------------------------------------------------
    def add_block(self, kind: str, text: str) -> _Block:
        block = _Block(
            block_id=f"{BLOCK_ID_PREFIX}{self.card_id or 'pending'}:{len(self.blocks)}:{uuid.uuid4().hex[:8]}",
            kind=kind,
            text=text,
        )
        self.blocks.append(block)
        if kind == "trace":
            self.trace_steps += _count_trace_steps(text)
        return block

    def find_block(self, block_id: str) -> Optional[_Block]:
        for block in self.blocks:
            if block.block_id == block_id:
                return block
        return None

    # -- rendering -------------------------------------------------------
    def trace_text(self) -> str:
        parts = [b.text for b in self.blocks if b.kind == "trace" and b.text]
        return _clip_tail(
            format_trace_lines("\n".join(parts)), MAX_TRACE_BYTES, _TRACE_TRUNCATED_NOTICE
        )

    def body_text(self) -> str:
        parts = [b.text for b in self.blocks if b.kind == "body" and b.text]
        return _clip_head(
            normalize_markdown("\n\n".join(parts)), MAX_BODY_BYTES, _TRUNCATED_NOTICE
        )

    def body_bytes(self) -> int:
        return sum(_byte_len(b.text) for b in self.blocks if b.kind == "body")


def _count_trace_steps(text: str) -> int:
    return len(_split_trace_steps(text))


def _byte_len(text: str) -> int:
    return len(text.encode("utf-8"))


#: Line prefixes that already make a line its own markdown block, so bulleting
#: them again would nest a list inside a list.
_BLOCK_PREFIXES = ("-", "*", "+", ">", "#", "|", "```", "~~~")
_ORDERED_ITEM_RE = re.compile(r"^\d{1,9}[.)]\s")
_FENCE_RE = re.compile(r"^(?:```|~~~)")


def _inline_code(text: str) -> str:
    """Wrap ``text`` in inline code with a delimiter the text can't break."""
    longest = 0
    run = 0
    for char in text:
        if char == "`":
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    ticks = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


def _consume_fence(lines: List[str], start: int, fence: str) -> Tuple[List[str], int]:
    """Collect the body of the fenced block opening at ``lines[start]``.

    Returns the body lines (outer blank lines dropped, inner indentation kept)
    and the index just past the closing fence.
    """
    body: List[str] = []
    index = start + 1
    while index < len(lines):
        line = lines[index].rstrip()
        if line.lstrip().startswith(fence):
            index += 1
            break
        body.append(line)
        index += 1
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    return body, index


def _split_trace_steps(text: str) -> List[str]:
    """Split accumulated progress text into rendered execution-trace steps.

    Each tool / thinking line the gateway produced is one step, and steps are
    separated by single newlines — which Feishu treats as soft breaks it may
    collapse.  A list item per step makes the separation structural rather
    than whitespace-dependent.

    One progress message can span several lines: on a markdown-capable
    platform the gateway renders a ``terminal`` call as a header line plus a
    fenced command block.  Treating those lines independently produced two
    defects in the panel — the command was bulleted *inside* the fence (it
    rendered as ``- <command>`` in the code box) and each of its lines counted
    as its own step, so a single shell call inflated the "N 步" summary.  A
    command that fits on one line is therefore folded onto its tool line as
    inline code, the way the delegate adapter already renders a tool call; a
    genuine multi-line script keeps its block but stays attached to that same
    step.

    Lines that are already a markdown block (a list item, quote, heading,
    table row) are passed through untouched.
    """
    steps: List[str] = []
    # True when the previous source line became a step that a code block on
    # the very next line belongs to. Reset by blank lines and by a block that
    # has already been folded, so a headerless fence (the gateway drops the
    # repeated header for back-to-back terminal calls) never attaches itself
    # to the preceding command.
    foldable = False
    lines = str(text or "").split("\n")
    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        stripped = line.lstrip()
        if not stripped:
            foldable = False
            index += 1
            continue
        if _FENCE_RE.match(stripped):
            body, index = _consume_fence(lines, index, stripped[:3])
            if not body:
                foldable = False
                continue
            if len(body) == 1:
                rendered = _inline_code(body[0].strip())
                if foldable:
                    head = steps[-1].rstrip()
                    joiner = " " if head.endswith(":") else ": "
                    steps[-1] = f"{head}{joiner}{rendered}"
                else:
                    steps.append(f"- {rendered}")
            else:
                fence = stripped[:3]
                block = "\n".join([fence, *body, fence])
                if foldable:
                    steps[-1] = f"{steps[-1]}\n{block}"
                else:
                    steps.append(block)
            foldable = False
            continue
        if stripped.startswith(_BLOCK_PREFIXES) or _ORDERED_ITEM_RE.match(stripped):
            steps.append(line)
        else:
            steps.append(f"- {stripped}")
        foldable = True
        index += 1
    return steps


def format_trace_lines(text: str) -> str:
    """Render accumulated progress text as the card's execution-trace list."""
    return "\n".join(_split_trace_steps(text))


def normalize_markdown(text: str) -> str:
    """Make agent markdown render the way Feishu's parser expects.

    Feishu's rich-text component is CommonMark, with one documented quirk that
    bites real agent output: a fenced code block is only recognized when the
    fence sits at the start of the line, and leading whitespace around the
    fence "may cause the code to fail to render".  Agents routinely indent a
    fence inside a list item, so de-indent the fence lines — the block itself
    is what the reader came for, and an indented fence renders as literal
    backticks.

    Everything else is left alone: headings, lists, tables, quotes and inline
    code all depend on the text arriving unmodified.
    """
    raw = str(text or "")
    if "```" not in raw:
        return raw
    lines = raw.split("\n")
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            lines[index] = stripped
    return "\n".join(lines)


def _clip_head(text: str, limit_bytes: int, notice: str) -> str:
    """Keep the beginning of ``text``, dropping the tail, within a byte cap.

    Body text must stay prefix-stable across updates or the CardKit
    ``content`` endpoint replaces the element instead of animating it, so the
    *head* is the part that has to survive.
    """
    if _byte_len(text) <= limit_bytes:
        return text
    budget = max(0, limit_bytes - _byte_len(notice))
    # ``errors="ignore"`` on the decode drops a partial trailing character
    # rather than emitting U+FFFD into the card.
    kept = text.encode("utf-8")[:budget].decode("utf-8", errors="ignore")
    return kept + notice


def _clip_tail(text: str, limit_bytes: int, notice: str) -> str:
    """Keep the end of ``text`` — for a trace, recent steps matter most."""
    if _byte_len(text) <= limit_bytes:
        return text
    budget = max(0, limit_bytes - _byte_len(notice))
    encoded = text.encode("utf-8")
    kept = encoded[len(encoded) - budget:].decode("utf-8", errors="ignore")
    return notice + kept


# ---------------------------------------------------------------------------
# Card JSON
# ---------------------------------------------------------------------------

def build_card_json(
    *,
    title: str,
    trace_text: str,
    body_text: str,
    copy: CardCopy,
    expanded: bool = True,
    streaming: bool = True,
    summary: str = "",
    minimal: bool = False,
) -> Dict[str, Any]:
    """Assemble the three-element card as JSON 2.0.

    ``minimal`` drops every field that is nice-to-have rather than load
    bearing.  It is the retry shape: if a Feishu tenant rejects an optional
    property (the send path is unforgiving about unknown keys) the second
    attempt still produces a usable card instead of dropping to plain text.
    """
    # ``update_multi`` must stay True: JSON 2.0 supports shared cards only, and
    # the streaming endpoint refuses an exclusive card outright (300302).
    config: Dict[str, Any] = {"streaming_mode": bool(streaming), "update_multi": True}
    if not minimal:
        config["summary"] = {"content": summary or copy.summary_running}
        config["streaming_config"] = dict(STREAMING_CONFIG)
        # 2.0's width knob.  ``wide_screen_mode`` is the 1.0 spelling and does
        # nothing here — and width is what decides whether a markdown table or
        # a code block reads or wraps into soup.
        config["width_mode"] = "fill"

    panel: Dict[str, Any] = {
        "tag": "collapsible_panel",
        "element_id": PANEL_ELEMENT_ID,
        # A collapsible panel without ``header.title`` is rejected outright
        # (code 10002, "no collapsible panel header").
        "header": {"title": {"tag": "plain_text", "content": copy.trace_title}},
        "elements": [
            {
                "tag": "markdown",
                "element_id": TRACE_ELEMENT_ID,
                "content": trace_text or copy.trace_placeholder,
            }
        ],
    }
    if not minimal:
        panel["expanded"] = bool(expanded)
        panel["padding"] = "6px 10px 6px 10px"
        panel["vertical_spacing"] = "4px"
        panel["border"] = {"color": "grey", "corner_radius": "6px"}
        panel["header"] = {
            "title": {"tag": "markdown", "content": f"**{copy.trace_title}**"},
            "vertical_align": "center",
            # The chevron rotates on expand, so the panel reads as openable
            # rather than as a mystery grey box.
            "icon": {
                "tag": "standard_icon",
                "token": "down-small-ccm_outlined",
                "size": "16px 16px",
            },
            "icon_position": "right",
            "icon_expanded_angle": -180,
        }
        # The trace is secondary to the answer; 12px keeps it legible without
        # competing with the body.
        panel["elements"][0]["text_size"] = "notation"

    body_element: Dict[str, Any] = {
        "tag": "markdown",
        "element_id": BODY_ELEMENT_ID,
        "content": body_text or copy.body_placeholder,
    }
    elements: List[Dict[str, Any]] = [panel]
    if not minimal:
        body_element["text_size"] = "normal"
        body_element["margin"] = "4px 0px 0px 0px"
    else:
        # Without the panel's border to separate the two areas, the minimal
        # shape needs an explicit rule between the trace and the answer.
        elements.append({"tag": "hr"})
    elements.append(body_element)

    body: Dict[str, Any] = {"elements": elements}
    if not minimal:
        body["padding"] = "12px 16px 12px 16px"
        body["vertical_spacing"] = "8px"

    card: Dict[str, Any] = {
        "schema": "2.0",
        "config": config,
        "header": {"title": {"tag": "plain_text", "content": title}},
        "body": body,
    }
    if not minimal:
        card["header"]["template"] = copy.template_running
        card["header"]["padding"] = "12px 16px 12px 16px"
    return card


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class FeishuCardOutputManager:
    """Owns the live card per route and routes adapter output into it.

    The adapter holds one manager.  A *route* is a chat (plus Feishu topic /
    thread when present) — the same identity the gateway uses to address a
    reply — and each route has at most one live card at a time.
    """

    def __init__(
        self,
        adapter: Any,
        *,
        copy: Optional[CardCopy] = None,
        flush_interval: float = DEFAULT_FLUSH_INTERVAL,
    ) -> None:
        self._adapter = adapter
        self._copy = copy or CardCopy()
        self._flush_interval = max(0.0, float(flush_interval))
        self._sessions: Dict[str, FeishuCardSession] = {}
        # Creating a card is a two-step remote handshake (create entity →
        # deliver into the chat), and the progress task and the stream
        # consumer both drive output concurrently.  Without this lock the
        # first trace line and the first body chunk of the same turn each
        # create their own card.
        self._creation_locks: Dict[str, asyncio.Lock] = {}
        self._blocks: Dict[str, str] = {}  # block_id → route_key
        self._active_turns: Dict[str, Dict[str, Any]] = {}
        self._chat_routes: Dict[str, str] = {}  # chat_id → most recent route_key
        self._route_lock = threading.RLock()
        self._cooldown_until: Dict[str, float] = {}

    # -- identity --------------------------------------------------------

    @staticmethod
    def route_key(chat_id: Any, thread_id: Any = None) -> str:
        return f"{str(chat_id or '')}\n{str(thread_id or '')}"

    def resolve_route(self, chat_id: Any, metadata: Optional[Dict[str, Any]]) -> str:
        """Map a ``send``/``edit`` call back onto a registered turn.

        Progress sends and content sends do not always carry identical
        metadata (the gateway derives thread routing separately for each), so
        an exact route match is tried first and a single active turn in the
        same chat is accepted as the fallback.
        """
        thread_id = (metadata or {}).get("thread_id")
        key = self.route_key(chat_id, thread_id)
        with self._route_lock:
            if key in self._active_turns or key in self._sessions:
                return key
            fallback = self._chat_routes.get(str(chat_id or ""))
            if fallback and (fallback in self._active_turns or fallback in self._sessions):
                return fallback
        return key

    # -- turn brackets ---------------------------------------------------

    def begin_turn(
        self,
        *,
        chat_id: str,
        thread_id: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Mark a route as inside an agent turn.

        Cards are only opened for output that belongs to a turn; slash-command
        replies, cron pushes and lifecycle notices keep the plain text path so
        a one-line notice does not become a card with an empty trace panel.
        """
        key = self.route_key(chat_id, thread_id)
        with self._route_lock:
            self._active_turns[key] = {
                "chat_id": str(chat_id or ""),
                "thread_id": thread_id,
                "reply_to": reply_to,
                "metadata": dict(metadata) if metadata else None,
            }
            self._chat_routes[str(chat_id or "")] = key
        return key

    def turn_active(self, route_key: str) -> bool:
        with self._route_lock:
            return route_key in self._active_turns

    async def finish_turn(
        self,
        *,
        chat_id: str,
        thread_id: Optional[str] = None,
        failed: bool = False,
    ) -> None:
        key = self.route_key(chat_id, thread_id)
        with self._route_lock:
            self._active_turns.pop(key, None)
        await self.close_route(key, failed=failed)

    # -- entry points ----------------------------------------------------

    async def deliver(
        self,
        *,
        chat_id: str,
        content: str,
        kind: str,
        owner: str = "main",
        title: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        open_outside_turn: bool = False,
    ) -> Optional[Tuple[str, str]]:
        """Append ``content`` to the live card for this route.

        Returns ``(block_id, card_message_id)`` on success, or ``None`` when
        the caller should fall back to the legacy text/post path.
        """
        if not self.available():
            return None
        text = str(content or "")
        if not text.strip():
            return None

        route = self.resolve_route(chat_id, metadata)
        if not open_outside_turn and not self.turn_active(route):
            return None
        if self._in_cooldown(route):
            return None

        session = await self._session_for(
            route_key=route,
            owner=owner,
            title=title,
            chat_id=chat_id,
            reply_to=reply_to,
            metadata=metadata,
        )
        if session is None:
            return None

        assert session.lock is not None
        async with session.lock:
            # A body block that would overflow the card rolls onto a
            # continuation card rather than being silently clipped.
            if kind == "body" and session.blocks and (
                session.body_bytes() + _byte_len(text) > MAX_BODY_BYTES
            ):
                await self._finalize_locked(session, failed=False)
                session = None  # type: ignore[assignment]

        if session is None:
            session = await self._session_for(
                route_key=route,
                owner=owner,
                title=self._continuation_title(title, owner),
                chat_id=chat_id,
                reply_to=reply_to,
                metadata=metadata,
                force_new=True,
            )
            if session is None:
                return None

        assert session.lock is not None
        async with session.lock:
            block = session.add_block(kind, text)
            with self._route_lock:
                self._blocks[block.block_id] = route
            self._mark_dirty(session, kind)
            self._schedule_flush(session)
            return block.block_id, session.message_id or block.block_id

    async def edit(self, *, message_id: str, content: str) -> Optional[bool]:
        """Rewrite the card block previously returned by :meth:`deliver`.

        ``None`` means "not a card block" — the adapter should treat the call
        as an ordinary message edit.
        """
        block_id = str(message_id or "")
        if not block_id.startswith(BLOCK_ID_PREFIX):
            return None
        with self._route_lock:
            route = self._blocks.get(block_id)
        if not route:
            return False
        session = self._sessions.get(route)
        if session is None or session.closed:
            return False
        assert session.lock is not None
        async with session.lock:
            block = session.find_block(block_id)
            if block is None:
                return False
            new_text = str(content or "")
            if block.text == new_text:
                return True
            if block.kind == "trace":
                session.trace_steps += _count_trace_steps(new_text) - _count_trace_steps(block.text)
            block.text = new_text
            self._mark_dirty(session, block.kind)
            self._schedule_flush(session)
            return True

    async def remove(self, *, message_id: str) -> Optional[bool]:
        """Retract a card block.

        The stream consumer replaces a long-lived preview by sending the
        finished answer as a fresh message and deleting the stale one
        (``_try_fresh_final``), and retracts a preview outright when the reply
        turns out to be a silence marker.  Both arrive here as a delete, and
        both would otherwise leave the superseded text sitting in the card
        beside its replacement.

        ``None`` means "not a card block"; ``False`` means the block's card is
        already sealed, so there is nothing left to retract.
        """
        block_id = str(message_id or "")
        if not block_id.startswith(BLOCK_ID_PREFIX):
            return None
        with self._route_lock:
            route = self._blocks.get(block_id)
        if not route:
            return False
        session = self._sessions.get(route)
        if session is None or session.closed:
            return False
        assert session.lock is not None
        async with session.lock:
            block = session.find_block(block_id)
            if block is None:
                return False
            if block.kind == "trace":
                session.trace_steps -= _count_trace_steps(block.text)
            session.blocks.remove(block)
            with self._route_lock:
                self._blocks.pop(block_id, None)
            self._mark_dirty(session, block.kind)
            self._schedule_flush(session)
            return True

    def owns_message(self, message_id: Any) -> bool:
        return str(message_id or "").startswith(BLOCK_ID_PREFIX)

    async def close_owner(
        self,
        *,
        chat_id: str,
        metadata: Optional[Dict[str, Any]] = None,
        owner: str,
        failed: bool = False,
    ) -> None:
        """Seal the live card iff it still belongs to ``owner``.

        A foreground delegate loop keeps one A2A context — and therefore one
        owner tag — across every exchange, so owner change alone cannot end a
        card there.  The delegate's turn-final event calls this to close the
        exchange it just finished; the next exchange then opens its own card.

        Scoped to the owner so a late event cannot seal a card that has since
        been handed to someone else.
        """
        route = self.resolve_route(chat_id, metadata)
        session = self._sessions.get(route)
        if session is None or session.closed or session.owner != owner:
            return
        assert session.lock is not None
        async with session.lock:
            if session.owner != owner:  # re-check under the lock
                return
            await self._finalize_locked(session, failed=failed)

    async def close_route(self, route_key: str, *, failed: bool = False) -> None:
        session = self._sessions.get(route_key)
        if session is None:
            return
        assert session.lock is not None
        async with session.lock:
            await self._finalize_locked(session, failed=failed)

    async def close_all(self, *, failed: bool = False) -> None:
        for route_key in list(self._sessions.keys()):
            try:
                await self.close_route(route_key, failed=failed)
            except Exception:
                logger.debug("[Feishu] card close failed for %s", route_key, exc_info=True)
        with self._route_lock:
            self._active_turns.clear()
            self._blocks.clear()
            self._chat_routes.clear()
            self._creation_locks.clear()

    # -- availability ----------------------------------------------------

    def available(self) -> bool:
        if not getattr(self._adapter, "_card_output_enabled", False):
            return False
        if getattr(self._adapter, "_client", None) is None:
            return False
        return load_cardkit()

    def _in_cooldown(self, route_key: str) -> bool:
        until = self._cooldown_until.get(route_key, 0.0)
        if until <= 0.0:
            return False
        if time.monotonic() >= until:
            self._cooldown_until.pop(route_key, None)
            return False
        return True

    def _enter_cooldown(self, route_key: str, reason: str) -> None:
        self._cooldown_until[route_key] = time.monotonic() + FAILURE_COOLDOWN_SECONDS
        logger.warning(
            "[Feishu] card output disabled for %ss on this route (%s); falling back to text/post",
            int(FAILURE_COOLDOWN_SECONDS), reason,
        )

    # -- session plumbing ------------------------------------------------

    def title_for(self, owner: str, *, agent_name: Optional[str] = None) -> str:
        if owner.startswith("delegate"):
            name = str(agent_name or "").strip() or self._copy.delegate_fallback_agent
            return self._copy.delegate_title.format(agent=name)
        return self._copy.main_title

    def _continuation_title(self, title: Optional[str], owner: str) -> str:
        base = title or self.title_for(owner)
        suffix = self._copy.continuation_suffix
        return base if base.endswith(suffix) else f"{base}{suffix}"

    def _creation_lock(self, route_key: str) -> asyncio.Lock:
        lock = self._creation_locks.get(route_key)
        if lock is None:
            lock = asyncio.Lock()
            self._creation_locks[route_key] = lock
        return lock

    async def _session_for(
        self,
        *,
        route_key: str,
        owner: str,
        title: Optional[str],
        chat_id: str,
        reply_to: Optional[str],
        metadata: Optional[Dict[str, Any]],
        force_new: bool = False,
    ) -> Optional[FeishuCardSession]:
        async with self._creation_lock(route_key):
            return await self._session_for_locked(
                route_key=route_key,
                owner=owner,
                title=title,
                chat_id=chat_id,
                reply_to=reply_to,
                metadata=metadata,
                force_new=force_new,
            )

    async def _session_for_locked(
        self,
        *,
        route_key: str,
        owner: str,
        title: Optional[str],
        chat_id: str,
        reply_to: Optional[str],
        metadata: Optional[Dict[str, Any]],
        force_new: bool = False,
    ) -> Optional[FeishuCardSession]:
        existing = self._sessions.get(route_key)
        if existing is not None and not existing.closed:
            if not force_new and existing.owner == owner:
                return existing
            # Speaker changed (main → delegate, delegate → main, or one
            # delegate to another): seal the live card so the next one is
            # unmistakably a different voice.
            assert existing.lock is not None
            async with existing.lock:
                await self._finalize_locked(existing, failed=False)

        turn = self._active_turns.get(route_key) or {}
        session = FeishuCardSession(
            route_key=route_key,
            owner=owner,
            title=title or self.title_for(owner),
            chat_id=str(chat_id or turn.get("chat_id") or ""),
            metadata=(dict(metadata) if metadata else turn.get("metadata")),
            reply_to=reply_to or turn.get("reply_to"),
        )
        session.lock = asyncio.Lock()
        created = await self._create_and_deliver(session)
        if not created:
            self._enter_cooldown(route_key, "card create/deliver failed")
            return None
        self._sessions[route_key] = session
        return session

    async def _create_and_deliver(self, session: FeishuCardSession) -> bool:
        card_id = await self._create_card(session, minimal=False)
        if not card_id:
            card_id = await self._create_card(session, minimal=True)
            if not card_id:
                return False
            session.rendered_trace = ""
            session.rendered_body = ""
        session.card_id = str(card_id)

        payload = json.dumps(
            {"type": "card", "data": {"card_id": session.card_id}}, ensure_ascii=False
        )
        try:
            response = await self._adapter._feishu_send_with_retry(
                chat_id=session.chat_id,
                msg_type="interactive",
                payload=payload,
                reply_to=session.reply_to,
                metadata=session.metadata,
            )
        except Exception as exc:
            logger.warning("[Feishu] card delivery raised: %s", exc, exc_info=True)
            return False
        if not self._adapter._response_succeeded(response):
            logger.warning(
                "[Feishu] card delivery rejected: [%s] %s",
                getattr(response, "code", "?"), getattr(response, "msg", ""),
            )
            return False
        session.message_id = str(
            self._adapter._extract_response_field(response, "message_id") or ""
        )
        return True

    async def _create_card(self, session: FeishuCardSession, *, minimal: bool) -> str:
        card = build_card_json(
            title=session.title,
            trace_text="",
            body_text="",
            copy=self._copy,
            expanded=True,
            streaming=True,
            summary=self._copy.summary_running,
            minimal=minimal,
        )
        body = (
            CreateCardRequestBody.builder()
            .type("card_json")
            .data(json.dumps(card, ensure_ascii=False))
            .build()
        )
        request = CreateCardRequest.builder().request_body(body).build()
        try:
            response = await self._adapter._run_blocking(
                self._adapter._client.cardkit.v1.card.create, request
            )
        except Exception as exc:
            logger.warning(
                "[Feishu] cardkit create raised (minimal=%s): %s", minimal, exc, exc_info=True
            )
            return ""
        if not self._adapter._response_succeeded(response):
            logger.warning(
                "[Feishu] cardkit create rejected (minimal=%s): [%s] %s",
                minimal, getattr(response, "code", "?"), getattr(response, "msg", ""),
            )
            return ""
        return str(self._adapter._extract_response_field(response, "card_id") or "")

    # -- flushing --------------------------------------------------------

    def _mark_dirty(self, session: FeishuCardSession, kind: str) -> None:
        if kind == "trace":
            session.trace_dirty = True
        else:
            session.body_dirty = True

    def _schedule_flush(self, session: FeishuCardSession) -> None:
        if session.flush_task is not None and not session.flush_task.done():
            return
        try:
            session.flush_task = asyncio.create_task(self._flush_loop(session))
        except RuntimeError:  # no running loop (defensive; tests)
            session.flush_task = None

    async def _flush_loop(self, session: FeishuCardSession) -> None:
        """Coalesce bursts of updates into one call per area per interval."""
        try:
            while True:
                wait = self._flush_interval - (time.monotonic() - session.last_flush_ts)
                if wait > 0:
                    await asyncio.sleep(wait)
                assert session.lock is not None
                async with session.lock:
                    if session.closed or not (session.trace_dirty or session.body_dirty):
                        session.flush_task = None
                        return
                    await self._render_locked(session)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.debug("[Feishu] card flush loop failed", exc_info=True)
            session.flush_task = None

    async def _render_locked(self, session: FeishuCardSession) -> None:
        """Push whichever areas changed, retrying a recoverable failure.

        A dropped update used to be lost for good: the dirty flag was cleared
        before the call and ``rendered_*`` only advanced on success, so the
        area sat stale until the next edit happened to arrive.  On a failure
        the flag goes back up (bounded) so the flush loop tries again.
        """
        session.last_flush_ts = time.monotonic()
        if session.trace_dirty:
            session.trace_dirty = False
            text = session.trace_text()
            if text != session.rendered_trace:
                if await self._update_trace(session, text):
                    session.rendered_trace = text
                    session.trace_retries = 0
                else:
                    self._requeue(session, "trace")
        if session.body_dirty:
            session.body_dirty = False
            text = session.body_text()
            if text != session.rendered_body:
                if await self._update_body_recovering(session, text):
                    session.rendered_body = text
                    session.body_retries = 0
                else:
                    self._requeue(session, "body")

    def _requeue(self, session: FeishuCardSession, kind: str) -> None:
        """Mark an area for another attempt, up to a bounded number of tries."""
        if kind == "trace":
            session.trace_retries += 1
            if session.trace_retries <= _MAX_RENDER_RETRIES:
                session.trace_dirty = True
            return
        session.body_retries += 1
        if session.body_retries <= _MAX_RENDER_RETRIES:
            session.body_dirty = True

    async def _update_body_recovering(self, session: FeishuCardSession, text: str) -> bool:
        """Stream the body, re-opening streaming mode if Feishu closed it.

        Feishu turns streaming mode off by itself once a card has been quiet
        for a while (200850 / 300309).  A turn that pauses on a slow tool would
        otherwise lose every remaining chunk of its answer.
        """
        ok, code = await self._body_stream_call(session, text)
        if ok or code not in _STREAMING_CLOSED_CODES:
            return ok
        logger.info("[Feishu] streaming mode was closed (%s); re-opening the card", code)
        if not await self._reopen_streaming(session):
            return False
        ok, _code = await self._body_stream_call(session, text)
        return ok

    async def _update_trace(self, session: FeishuCardSession, text: str) -> bool:
        """Replace the trace element wholesale.

        ``PUT /elements/:id`` is the right verb here: the gateway hands us the
        *cumulative* progress text for the current bubble, so there is nothing
        to append — and unlike the streaming ``content`` endpoint this works
        with ``streaming_mode`` already turned off during finalize.
        """
        element = {
            "tag": "markdown",
            "element_id": TRACE_ELEMENT_ID,
            "content": normalize_markdown(text) or self._copy.trace_placeholder,
            "text_size": "notation",
        }
        body = (
            UpdateCardElementRequestBody.builder()
            .element(json.dumps(element, ensure_ascii=False))
            .uuid(str(uuid.uuid4()))
            .sequence(session.next_sequence())
            .build()
        )
        request = (
            UpdateCardElementRequest.builder()
            .card_id(session.card_id)
            .element_id(TRACE_ELEMENT_ID)
            .request_body(body)
            .build()
        )
        return await self._call(
            session, self._adapter._client.cardkit.v1.card_element.update, request, "trace update"
        )

    async def _body_stream_call(
        self, session: FeishuCardSession, text: str
    ) -> Tuple[bool, Optional[int]]:
        """Stream the body text as markdown, surfacing the error code.

        ``content`` is the element's **complete text, as a plain string** —
        not a JSON object.  Wrapping it (``{"text": ...}``) is accepted with
        code 0 and then rendered literally, wrapper braces and escaped
        newlines included, which is exactly what a card showing raw JSON is
        telling you.

        Full text every time is deliberate: when the new text extends the old
        one Feishu animates the difference as a typewriter, and otherwise it
        replaces the element.  Never a delta.
        """
        body = (
            ContentCardElementRequestBody.builder()
            # Never send an empty string (the API requires at least one
            # character): a retraction that empties the body — a silence
            # marker, or a superseded preview — still has to leave a valid
            # element behind, so blank it with a space instead.
            .content(text or " ")
            .uuid(str(uuid.uuid4()))
            .sequence(session.next_sequence())
            .build()
        )
        request = (
            ContentCardElementRequest.builder()
            .card_id(session.card_id)
            .element_id(BODY_ELEMENT_ID)
            .request_body(body)
            .build()
        )
        return await self._body_stream_call_request(request)

    async def _body_stream_call_request(self, request: Any) -> Tuple[bool, Optional[int]]:
        return await self._call_with_code(
            self._adapter._client.cardkit.v1.card_element.content, request, "body stream"
        )

    async def _reopen_streaming(self, session: FeishuCardSession) -> bool:
        """Turn ``streaming_mode`` back on after Feishu's inactivity timeout."""
        settings = {"config": {"streaming_mode": True}}
        body = (
            SettingsCardRequestBody.builder()
            .settings(json.dumps(settings, ensure_ascii=False))
            .uuid(str(uuid.uuid4()))
            .sequence(session.next_sequence())
            .build()
        )
        request = (
            SettingsCardRequest.builder()
            .card_id(session.card_id)
            .request_body(body)
            .build()
        )
        ok, _code = await self._call_with_code(
            self._adapter._client.cardkit.v1.card.settings, request, "reopen streaming"
        )
        return ok

    async def _collapse_panel(self, session: FeishuCardSession) -> bool:
        title = (
            self._copy.trace_title_done.format(steps=session.trace_steps)
            if session.trace_steps
            else self._copy.trace_title_empty
        )
        partial = {
            "expanded": False,
            "header": {"title": {"tag": "plain_text", "content": title}},
        }
        body = (
            PatchCardElementRequestBody.builder()
            .partial_element(json.dumps(partial, ensure_ascii=False))
            .uuid(str(uuid.uuid4()))
            .sequence(session.next_sequence())
            .build()
        )
        request = (
            PatchCardElementRequest.builder()
            .card_id(session.card_id)
            .element_id(PANEL_ELEMENT_ID)
            .request_body(body)
            .build()
        )
        return await self._call(
            session, self._adapter._client.cardkit.v1.card_element.patch, request, "panel collapse"
        )

    async def _apply_settings(self, session: FeishuCardSession, *, failed: bool) -> bool:
        settings = {
            "config": {
                "streaming_mode": False,
                "summary": {
                    "content": self._copy.summary_failed if failed else self._copy.summary_done
                },
            }
        }
        body = (
            SettingsCardRequestBody.builder()
            .settings(json.dumps(settings, ensure_ascii=False))
            .uuid(str(uuid.uuid4()))
            .sequence(session.next_sequence())
            .build()
        )
        request = (
            SettingsCardRequest.builder()
            .card_id(session.card_id)
            .request_body(body)
            .build()
        )
        return await self._call(
            session, self._adapter._client.cardkit.v1.card.settings, request, "settings"
        )

    async def _call(self, session: FeishuCardSession, method: Any, request: Any, label: str) -> bool:
        del session  # kept for call-site symmetry; failures are handled by area
        ok, _code = await self._call_with_code(method, request, label)
        return ok

    async def _call_with_code(
        self, method: Any, request: Any, label: str
    ) -> Tuple[bool, Optional[int]]:
        """Invoke one CardKit endpoint, returning success plus the error code.

        The code matters: some rejections are recoverable in place (streaming
        mode timed out, or the user is mid-interaction with the card) and the
        caller retries instead of dropping the rest of the answer.
        """
        try:
            response = await self._adapter._run_blocking(method, request)
        except Exception as exc:
            logger.warning("[Feishu] card %s raised: %s", label, exc, exc_info=True)
            return False, None
        if not self._adapter._response_succeeded(response):
            raw_code = getattr(response, "code", None)
            try:
                code = int(raw_code)
            except (TypeError, ValueError):
                code = None
            logger.warning(
                "[Feishu] card %s rejected: [%s] %s",
                label, raw_code, getattr(response, "msg", ""),
            )
            return False, code
        return True, None

    # -- finalize --------------------------------------------------------

    async def _finalize_locked(self, session: FeishuCardSession, *, failed: bool) -> None:
        if session.closed:
            return
        session.closed = True
        task = session.flush_task
        session.flush_task = None
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()

        # One last render so nothing streamed after the previous flush is lost,
        # then collapse the trace and stop the streaming animation.
        try:
            session.trace_dirty = session.trace_dirty or bool(session.trace_text())
            session.body_dirty = session.body_dirty or bool(session.body_text())
            await self._render_locked(session)
            if not session.panel_collapsed:
                session.panel_collapsed = await self._collapse_panel(session)
            await self._apply_settings(session, failed=failed)
        except Exception:
            logger.debug("[Feishu] card finalize failed", exc_info=True)

        with self._route_lock:
            if self._sessions.get(session.route_key) is session:
                self._sessions.pop(session.route_key, None)
            for block in session.blocks:
                self._blocks.pop(block.block_id, None)


__all__ = [
    "BLOCK_ID_PREFIX",
    "BODY_ELEMENT_ID",
    "CARDKIT_AVAILABLE",
    "CardCopy",
    "FeishuCardOutputManager",
    "FeishuCardSession",
    "MAX_BODY_BYTES",
    "MAX_TRACE_BYTES",
    "STREAMING_CONFIG",
    "PANEL_ELEMENT_ID",
    "TRACE_ELEMENT_ID",
    "build_card_json",
    "format_trace_lines",
    "load_cardkit",
    "normalize_markdown",
]
