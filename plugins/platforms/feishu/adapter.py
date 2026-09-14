"""
Feishu/Lark platform adapter.

Supports:
- WebSocket long connection and Webhook transport
- Direct-message and group @mention-gated text receive/send
- Inbound image/file/audio/media caching
- Gateway allowlist integration via FEISHU_ALLOWED_USERS
- Persistent dedup state across restarts
- Per-chat serial message processing (matches openclaw createChatQueue)
- Processing status reactions: Typing while working, removed on success,
  swapped for CrossMark on failure
- Reaction events routed as synthetic text events (matches openclaw)
- Interactive card button-click events routed as synthetic COMMAND events
- Webhook anomaly tracking (matches openclaw createWebhookAnomalyTracker)
- Verification token validation as second auth layer (matches openclaw)

Feishu identity model
---------------------
Feishu uses three user-ID tiers (official docs:
https://open.feishu.cn/document/home/user-identity-introduction/introduction):

  open_id  (ou_xxx)  — **App-scoped**.  The same person gets a different
                        open_id under each Feishu app.  Always available in
                        event payloads without extra permissions.
  user_id  (u_xxx)   — **Tenant-scoped**.  Stable within a company but
                        requires the ``contact:user.employee_id:readonly``
                        scope.  May not be present.
  union_id (on_xxx)  — **Developer-scoped**.  Same across all apps owned by
                        one developer/ISV.  Best cross-app stable ID.

For bots specifically:

  app_id              — The application's canonical credential identifier.
  bot open_id         — Returned by ``/bot/v3/info``.  This is the bot's own
                        open_id *within its app context* and is what Feishu
                        puts in ``mentions[].id.open_id`` when someone
                        @-mentions the bot.  Used for mention gating only.

In single-bot mode (what Hermes currently supports), open_id works as a
de-facto unique user identifier since there is only one app context.

Session-key participant isolation prefers ``union_id`` (via user_id_alt)
over ``open_id`` (via user_id) so that sessions stay stable if the same
user is seen through different apps in the future.
"""

from __future__ import annotations

import asyncio
import collections
import concurrent.futures
import hashlib
import hmac
import itertools
import json
import logging
import mimetypes
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# aiohttp/websockets are independent optional deps — import outside lark_oapi
# so they remain available for tests and webhook mode even if lark_oapi is missing.
try:
    import aiohttp
    from aiohttp import web
except ImportError:
    aiohttp = None  # type: ignore[assignment]
    web = None  # type: ignore[assignment]

try:
    import websockets
except ImportError:
    websockets = None  # type: ignore[assignment]

# lark_oapi takes a noticeable amount of time to import.  Keep the gateway
# configuration path responsive by importing it only when Feishu connects.
lark = None  # type: ignore[assignment]
GetApplicationRequest = None  # type: ignore[assignment]
CreateFileRequest = None  # type: ignore[assignment]
CreateFileRequestBody = None  # type: ignore[assignment]
CreateImageRequest = None  # type: ignore[assignment]
CreateImageRequestBody = None  # type: ignore[assignment]
CreateMessageRequest = None  # type: ignore[assignment]
CreateMessageRequestBody = None  # type: ignore[assignment]
GetChatRequest = None  # type: ignore[assignment]
GetMessageRequest = None  # type: ignore[assignment]
GetMessageResourceRequest = None  # type: ignore[assignment]
P2ImMessageMessageReadV1 = None  # type: ignore[assignment]
ReplyMessageRequest = None  # type: ignore[assignment]
ReplyMessageRequestBody = None  # type: ignore[assignment]
UpdateMessageRequest = None  # type: ignore[assignment]
UpdateMessageRequestBody = None  # type: ignore[assignment]
AccessTokenType = None  # type: ignore[assignment]
HttpMethod = None  # type: ignore[assignment]
FEISHU_DOMAIN = None  # type: ignore[assignment]
LARK_DOMAIN = None  # type: ignore[assignment]
BaseRequest = None  # type: ignore[assignment]
CallBackCard = None  # type: ignore[assignment]
P2CardActionTriggerResponse = None  # type: ignore[assignment]
EventDispatcherHandler = None  # type: ignore[assignment]
FeishuWSClient = None  # type: ignore[assignment]
FEISHU_AVAILABLE = False
_lark_import_lock = threading.Lock()

FEISHU_WEBSOCKET_AVAILABLE = websockets is not None
FEISHU_WEBHOOK_AVAILABLE = aiohttp is not None

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    ProcessingOutcome,
    SendResult,
    SUPPORTED_DOCUMENT_TYPES,
    cache_document_from_bytes,
    cache_image_from_url,
    cache_audio_from_bytes,
    cache_image_from_bytes,
    should_send_media_as_audio,
)
from gateway.status import acquire_scoped_lock, release_scoped_lock
from hermes_constants import get_hermes_home
from utils import atomic_json_write, env_float, env_int


from agent.secret_scope import UnscopedSecretError as _UnscopedSecretError
from agent.secret_scope import get_secret as _scoped_get_secret


def _get_scoped_secret(name, default=None):
    """Scope-aware credential read with the default-profile startup fallback.

    Secondary profiles construct their adapters under a profile secret
    scope -- the scope is authoritative and a scoped miss returns ``default``
    (no cross-profile borrow from ``os.environ``, which may hold another
    profile's value). The DEFAULT profile's adapter constructs and sends
    *unscoped* under multiplexing, where a bare ``get_secret`` would raise
    ``UnscopedSecretError`` and crash this path; there ``os.environ`` is that
    profile's own value, so fall back to it. Same pattern as the Slack
    ``SLACK_APP_TOKEN`` read (#59739) and
    ``gateway/platforms/whatsapp_common.py::_get_wsecret``.
    """
    try:
        val = _scoped_get_secret(name, default)
    except _UnscopedSecretError:
        val = os.getenv(name)
    return val if val is not None else default


logger = logging.getLogger(__name__)
DEFAULT_DELEGATE_STREAM_EDIT_INTERVAL = 3.0

# Card output (see feishu_cardkit.py).  One CardKit card per speaker per
# turn: header title, collapsible execution trace, streamed rich-text body.
_DEFAULT_CARD_FLUSH_INTERVAL = 0.4
# Metadata key the gateway sets on tool-progress / thinking sends so the
# adapter can tell execution chrome from the reply itself.  Absent means
# "this is content", which keeps every other caller on the body path.
_CARD_PROGRESS_METADATA_KEY = "hermes_progress"
# Metadata key a caller sets to keep a send out of the card entirely.  For
# transient status notices — a liveness heartbeat, an interaction ack — which
# are not the agent's reply and would otherwise splice themselves into the
# middle of whatever answer is streaming.  The platform-neutral
# ``non_conversational`` marker is honoured the same way.
_CARD_BYPASS_METADATA_KEY = "hermes_card_bypass"
_card_output_module: Any = None

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_MARKDOWN_HINT_RE = re.compile(
    # Pipe table: any header line + separator line both starting with '|'.
    r"(^\|.*\|\s*\n\|[-:|\s]+\|)"
    # Headings, lists, code, bold/italic/strike/underline, links, blockquotes.
    r"|(^#{1,6}\s)"
    r"|(^\s*[-*]\s)"
    r"|(^\s*\d+\.\s)"
    r"|(^\s*---+\s*$)"
    r"|(```)"
    r"|(`[^`\n]+`)"
    r"|(\*\*[^*\n].+?\*\*)"
    r"|(~~[^~\n].+?~~)"
    r"|(<u>.+?</u>)"
    r"|(\*[^*\n]+\*)"
    r"|(\[[^\]]+\]\([^)]+\))"
    r"|(^>\s)",
    re.MULTILINE,
)
# Backwards-compatible alias retained because external callers reference it.
_MARKDOWN_TABLE_RE = re.compile(r"^\|.*\|\n\|[-|: ]+\|", re.MULTILINE)
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MARKDOWN_FENCE_OPEN_RE = re.compile(r"^```([^\n`]*)\s*$")
_MARKDOWN_FENCE_CLOSE_RE = re.compile(r"^```\s*$")
_MENTION_RE = re.compile(r"@_user_\d+")
_MULTISPACE_RE = re.compile(r"[ \t]{2,}")
_POST_CONTENT_INVALID_RE = re.compile(r"content format of the post type is incorrect", re.IGNORECASE)
# ---------------------------------------------------------------------------
# Media type sets and upload constants
# ---------------------------------------------------------------------------

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_AUDIO_EXTENSIONS = {".ogg", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".opus", ".webm"}
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".3gp"}
_DOCUMENT_MIME_TO_EXT = {mime: ext for ext, mime in SUPPORTED_DOCUMENT_TYPES.items()}
_FEISHU_IMAGE_UPLOAD_TYPE = "message"
_FEISHU_FILE_UPLOAD_TYPE = "stream"
_FEISHU_OPUS_UPLOAD_EXTENSIONS = {".ogg", ".opus"}
_FEISHU_MEDIA_UPLOAD_EXTENSIONS = {".mp4", ".mov", ".avi", ".m4v"}
_FEISHU_DOC_UPLOAD_TYPES = {
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "doc",
    ".xls": "xls",
    ".xlsx": "xls",
    ".ppt": "ppt",
    ".pptx": "ppt",
}
# Card-mode attachment routing.  Deliberately the same sets the gateway's own
# dispatch loop uses, so a file leaves the turn as the same message type
# whether card output is on or off.
_CARD_ATTACHMENT_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".3gp"}
_CARD_ATTACHMENT_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
# ---------------------------------------------------------------------------
# Connection, retry and batching tuning
# ---------------------------------------------------------------------------

_MAX_TEXT_INJECT_BYTES = 100 * 1024
_FEISHU_CONNECT_ATTEMPTS = 3
_FEISHU_SEND_ATTEMPTS = 3
_FEISHU_APP_LOCK_SCOPE = "feishu-app-id"
_DEFAULT_TEXT_BATCH_DELAY_SECONDS = 0.6
_DEFAULT_TEXT_BATCH_MAX_MESSAGES = 8
_DEFAULT_TEXT_BATCH_MAX_CHARS = 4000
_DEFAULT_MEDIA_BATCH_DELAY_SECONDS = 0.8
_DEFAULT_DEDUP_CACHE_SIZE = 2048
_DEFAULT_WEBHOOK_HOST = "127.0.0.1"
_DEFAULT_WEBHOOK_PORT = 8765
_DEFAULT_WEBHOOK_PATH = "/feishu/webhook"
# ---------------------------------------------------------------------------
# TTL, rate-limit and webhook security constants
# ---------------------------------------------------------------------------

_FEISHU_DEDUP_TTL_SECONDS = 24 * 60 * 60          # 24 hours — matches openclaw
_FEISHU_SENDER_NAME_TTL_SECONDS = 10 * 60          # 10 minutes sender-name cache
_FEISHU_WEBHOOK_MAX_BODY_BYTES = 1 * 1024 * 1024   # 1 MB body limit
_FEISHU_WEBHOOK_RATE_WINDOW_SECONDS = 60            # sliding window for rate limiter
_FEISHU_WEBHOOK_RATE_LIMIT_MAX = 120               # max requests per window per IP — matches openclaw
_FEISHU_WEBHOOK_RATE_MAX_KEYS = 4096               # max tracked keys (prevents unbounded growth)
_FEISHU_WEBHOOK_BODY_TIMEOUT_SECONDS = 30          # max seconds to read request body
_FEISHU_WEBHOOK_ANOMALY_THRESHOLD = 25             # consecutive error responses before WARNING log
_FEISHU_WEBHOOK_ANOMALY_TTL_SECONDS = 6 * 60 * 60  # anomaly tracker TTL (6 hours) — matches openclaw
_FEISHU_CARD_ACTION_DEDUP_TTL_SECONDS = 15 * 60    # card action token dedup window (15 min)

_APPROVAL_CHOICE_MAP: Dict[str, str] = {
    "approve_once": "once",
    "approve_session": "session",
    "approve_always": "always",
    "deny": "deny",
}
_APPROVAL_LABEL_MAP: Dict[str, str] = {
    "once": "Approved once",
    "session": "Approved for session",
    "always": "Approved permanently",
    "deny": "Denied",
}


async def _read_limited_feishu_webhook_body(request: Any, max_bytes: int) -> bytes:
    """Read at most ``max_bytes`` from an aiohttp request body."""
    try:
        body = await request.content.readexactly(max_bytes + 1)
    except asyncio.IncompleteReadError as exc:
        body = exc.partial
    if len(body) > max_bytes:
        raise ValueError("payload too large")
    return body


_FEISHU_BOT_MSG_TRACK_SIZE = 512                   # LRU size for tracking sent message IDs
_FEISHU_REPLY_FALLBACK_CODES = frozenset({230011, 231003})  # reply target withdrawn/missing → create fallback
#: A topic (``omt_*``) accepts text and cards as ``receive_id_type=thread_id``
#: but rejects an attachment keyed that way with a bare field-validation
#: error.  The reply API places the same upload inside the topic fine, so
#: this code means "re-anchor", not "give up" — see
#: ``_send_attachment_message``.
_FEISHU_ATTACHMENT_THREAD_RECEIVE_CODE = 99992402

# Feishu reactions render as prominent badges, unlike Discord/Telegram's
# small footer emoji — a success badge on every message would add noise, so
# we only mark start (Typing) and failure (CrossMark); the reply itself is
# the success signal.
_FEISHU_REACTION_IN_PROGRESS = "Typing"
_FEISHU_REACTION_FAILURE = "CrossMark"
# Bound on the (message_id → reaction_id) handle cache. Happy-path entries
# drain on completion; the cap is a safeguard against unbounded growth from
# delete-failures, not a capacity plan.
_FEISHU_PROCESSING_REACTION_CACHE_SIZE = 1024
_FEISHU_MESSAGE_TEXT_CACHE_SIZE = 512       # LRU cap for reply-context message text lookups

# QR onboarding constants
_ONBOARD_ACCOUNTS_URLS = {
    "feishu": "https://accounts.feishu.cn",
    "lark": "https://accounts.larksuite.com",
}
_ONBOARD_OPEN_URLS = {
    "feishu": "https://open.feishu.cn",
    "lark": "https://open.larksuite.com",
}
_REGISTRATION_PATH = "/oauth/v1/app/registration"
_ONBOARD_REQUEST_TIMEOUT_S = 10

# ---------------------------------------------------------------------------
# Fallback display strings
# ---------------------------------------------------------------------------

FALLBACK_POST_TEXT = "[Rich text message]"
FALLBACK_FORWARD_TEXT = "[Merged forward message]"
FALLBACK_SHARE_CHAT_TEXT = "[Shared chat]"
FALLBACK_INTERACTIVE_TEXT = "[Interactive message]"
FALLBACK_IMAGE_TEXT = "[Image]"
FALLBACK_ATTACHMENT_TEXT = "[Attachment]"
# ---------------------------------------------------------------------------
# Post/card parsing helpers
# ---------------------------------------------------------------------------

_PREFERRED_LOCALES = ("zh_cn", "en_us")
_MARKDOWN_SPECIAL_CHARS_RE = re.compile(r"([\\`*_{}\[\]()#+\-!|>~])")
_MENTION_PLACEHOLDER_RE = re.compile(r"@_user_\d+")
_MENTION_BOUNDARY_CHARS = frozenset(" \t\n\r.,;:!?、，。；：！？()[]{}<>\"'`")
_TRAILING_TERMINAL_PUNCT = frozenset(" \t\n\r.!?。！？")
_WHITESPACE_RE = re.compile(r"\s+")
_SUPPORTED_CARD_TEXT_KEYS = (
    "title",
    "text",
    "content",
    "label",
    "value",
    "name",
    "summary",
    "subtitle",
    "description",
    "placeholder",
    "hint",
)
_SKIP_TEXT_KEYS = {
    "tag",
    "type",
    "msg_type",
    "message_type",
    "chat_id",
    "open_chat_id",
    "share_chat_id",
    "file_key",
    "image_key",
    "user_id",
    "open_id",
    "union_id",
    "url",
    "href",
    "link",
    "token",
    "template",
    "locale",
}


@dataclass(frozen=True)
class FeishuPostMediaRef:
    file_key: str
    file_name: str = ""
    resource_type: str = "file"


@dataclass(frozen=True)
class FeishuMentionRef:
    name: str = ""
    open_id: str = ""
    is_all: bool = False
    is_self: bool = False


@dataclass(frozen=True)
class _FeishuBotIdentity:
    open_id: str = ""
    user_id: str = ""
    name: str = ""

    def matches(self, *, open_id: str, user_id: str, name: str) -> bool:
        # Precedence: open_id > user_id > name. IDs are authoritative when both
        # sides have them; the next tier is only considered when either side
        # lacks the current one.
        if open_id and self.open_id:
            return open_id == self.open_id
        if user_id and self.user_id:
            return user_id == self.user_id
        return bool(self.name) and name == self.name


@dataclass(frozen=True)
class FeishuPostParseResult:
    text_content: str
    image_keys: List[str] = field(default_factory=list)
    media_refs: List[FeishuPostMediaRef] = field(default_factory=list)


@dataclass(frozen=True)
class FeishuNormalizedMessage:
    raw_type: str
    text_content: str
    preferred_message_type: str = "text"
    image_keys: List[str] = field(default_factory=list)
    media_refs: List[FeishuPostMediaRef] = field(default_factory=list)
    mentions: List[FeishuMentionRef] = field(default_factory=list)
    relation_kind: str = "plain"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FeishuAdapterSettings:
    app_id: str  # Canonical bot/app identifier (credential, not from event payloads)
    app_secret: str
    domain_name: str
    connection_mode: str
    encrypt_key: str
    verification_token: str
    group_policy: str
    allowed_group_users: frozenset[str]
    # Bot's own open_id (app-scoped) — returned by /bot/v3/info.  Used only for
    # @mention matching: Feishu puts this value in mentions[].id.open_id when
    # a user @-mentions the bot in a group chat.
    bot_open_id: str
    # Bot's user_id (tenant-scoped) — optional, used as fallback mention match.
    bot_user_id: str
    bot_name: str
    dedup_cache_size: int
    text_batch_delay_seconds: float
    text_batch_split_delay_seconds: float
    text_batch_max_messages: int
    text_batch_max_chars: int
    media_batch_delay_seconds: float
    webhook_host: str
    webhook_port: int
    webhook_path: str
    ws_reconnect_nonce: int = 30
    ws_reconnect_interval: int = 120
    ws_ping_interval: Optional[int] = None
    ws_ping_timeout: Optional[int] = None
    admins: frozenset[str] = frozenset()
    default_group_policy: str = ""
    group_rules: Dict[str, FeishuGroupRule] = field(default_factory=dict)
    allow_bots: str = "none"  # "none" | "mentions" | "all"
    require_mention: bool = True
    reply_thread: bool = True
    # Render agent output as a three-element CardKit card instead of
    # text/post messages.  Opt-in: the text/post path stays the default, and
    # remains the automatic fallback whenever a card call fails.
    card_output: bool = False


@dataclass
class FeishuGroupRule:
    """Per-group policy rule for controlling which users may interact with the bot."""

    policy: str  # "open" | "allowlist" | "blacklist" | "admin_only" | "disabled"
    allowlist: set[str] = field(default_factory=set)
    blacklist: set[str] = field(default_factory=set)
    require_mention: Optional[bool] = None  # None = inherit global


@dataclass
class FeishuBatchState:
    events: Dict[str, MessageEvent] = field(default_factory=dict)
    tasks: Dict[str, asyncio.Task] = field(default_factory=dict)
    counts: Dict[str, int] = field(default_factory=dict)


@dataclass
class _FeishuDelegateRoute:
    key: str
    chat_id: str
    thread_id: Optional[str]
    user_id: Optional[str]
    chat_type: Optional[str]
    input_adapter: Any


@dataclass
class _FeishuDelegateStreamState:
    accumulated_text: str = ""
    pending_text: str = ""
    message_id: Optional[str] = None
    last_rendered_text: str = ""
    lock: Optional[asyncio.Lock] = None
    last_flush_ts: float = 0.0
    flush_task: Optional[asyncio.Task] = None


class _FeishuDelegateInputAdapter:
    """Bridge async Feishu events to the synchronous A2A foreground loop."""

    def __init__(self, adapter: "FeishuAdapter", route: _FeishuDelegateRoute):
        self._adapter = adapter
        self._route = route
        self._condition = threading.Condition()
        self._lines: List[str] = []
        self._closed = False
        self._last_read_timed_out = False

    def enter_foreground(self) -> bool:
        self._adapter._register_delegate_route(self._route)
        return True

    def exit_foreground(self) -> None:
        self._adapter._unregister_delegate_route(self._route)

    def push_line(self, text: str) -> bool:
        with self._condition:
            if self._closed:
                return False
            self._lines.append(str(text or ""))
            self._condition.notify_all()
            return True

    def read_line(self, timeout=None):
        with self._condition:
            self._last_read_timed_out = False
            deadline = None if timeout is None else time.monotonic() + max(0.0, float(timeout))
            while not self._lines and not self._closed:
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._last_read_timed_out = True
                    return None
                self._condition.wait(timeout=remaining)
            if self._lines:
                return self._lines.pop(0)
            return None

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        self.exit_foreground()

    def last_read_timed_out(self) -> bool:
        with self._condition:
            return self._last_read_timed_out


class _FeishuDelegateOutputAdapter:
    """Render A2A delegate events back into the originating Feishu surface."""

    def __init__(
        self,
        adapter: "FeishuAdapter",
        *,
        chat_id: str,
        thread_id: Optional[str],
        user_id: Optional[str] = None,
        chat_type: Optional[str] = None,
    ):
        self._adapter = adapter
        self._chat_id = chat_id
        self._thread_id = thread_id
        self._user_id = user_id
        self._chat_type = chat_type
        self._a2a_session = None

    def bind_a2a_interaction_session(self, session) -> None:
        self._a2a_session = session

    def unbind_a2a_interaction_session(self, session) -> None:
        if self._a2a_session is session:
            self._a2a_session = None

    def emit(self, source, event_type, content, session_id=None) -> None:
        self._adapter._schedule_delegate_output(
            self._emit_async(
                str(source or ""), str(event_type or ""), str(content or ""), session_id=session_id
            )
        )

    def _delegate_agent_name(self) -> Optional[str]:
        """Best-effort remote agent name for the delegate card title."""
        name = getattr(self._a2a_session, "agent_name", None)
        return str(name).strip() or None if name else None

    def _route_key(self) -> str:
        return self._adapter._delegate_route_key(
            chat_id=self._chat_id,
            thread_id=self._thread_id,
            user_id=self._user_id,
            chat_type=self._chat_type,
        )

    @staticmethod
    def _format_tool_call(content: str) -> str:
        tool_name, _sep, raw_args = str(content or "").strip().partition(" ")
        tool_name = tool_name.strip() or "tool"
        preview = " ".join(raw_args.strip().split())
        if len(preview) > 80:
            preview = preview[:77] + "..."
        return f"`tool` {tool_name}: {preview}" if preview else f"`tool` {tool_name}"

    async def _emit_async(self, source: str, event_type: str, content: str, *, session_id=None) -> None:
        metadata = {"thread_id": self._thread_id} if self._thread_id else None
        if source == "delegate" and event_type in {
            "approval_request",
            "clarify_request",
            "approval_resolved",
            "clarify_resolved",
        }:
            try:
                payload = json.loads(content)
            except (TypeError, ValueError):
                payload = {}
            responder = getattr(self._a2a_session, "schedule_interaction_response", None)
            if event_type == "approval_request" and payload:
                await self._adapter.send_delegate_exec_approval(
                    chat_id=self._chat_id,
                    payload=payload,
                    responder=responder if callable(responder) else None,
                    metadata=metadata,
                    user_id=self._user_id,
                )
            elif event_type == "clarify_request" and payload:
                await self._adapter.send_delegate_clarify(
                    chat_id=self._chat_id,
                    payload=payload,
                    responder=responder if callable(responder) else None,
                    metadata=metadata,
                    user_id=self._user_id,
                )
            elif event_type.endswith("_resolved") and payload:
                await self._adapter.resolve_delegate_interaction(payload)
            return

        if source == "delegate" and event_type in {
            "ai_delta", "ai", "tool_call", "status", "error",
        }:
            # Card path: every delegated agent writes into its own card,
            # keyed by A2A context id, so its trace and answer never mix
            # with the main agent's.  Falls through to the legacy
            # message-per-event path when cards are unavailable.
            try:
                handled = await self._adapter.handle_delegate_card_event(
                    chat_id=self._chat_id,
                    metadata=metadata,
                    owner=self._adapter._card_delegate_owner(session_id),
                    agent_name=self._delegate_agent_name(),
                    event_type=event_type,
                    content=content,
                )
            except Exception:
                logger.debug(
                    "[Feishu] delegate card event failed; using messages", exc_info=True
                )
                handled = False
            if handled:
                return

        del session_id
        if source == "delegate" and event_type == "ai_delta":
            if content:
                await self._adapter.handle_delegate_ai_delta(
                    route_key=self._route_key(), chat_id=self._chat_id, content=content, metadata=metadata
                )
            return
        if source == "delegate" and event_type == "ai":
            await self._adapter.handle_delegate_stream_segment_break(
                route_key=self._route_key(), chat_id=self._chat_id, metadata=metadata
            )
            return
        if source == "delegate" and event_type == "tool_call":
            await self._adapter.handle_delegate_stream_segment_break(
                route_key=self._route_key(), chat_id=self._chat_id, metadata=metadata
            )
            await self._adapter.send(self._chat_id, self._format_tool_call(content), metadata=metadata)
            return
        prefix = {"status": "_delegate_", "error": "`delegate error`"}.get(
            event_type, f"`{event_type}`"
        )
        await self._adapter.send(self._chat_id, f"{prefix}: {content}", metadata=metadata)


# ---------------------------------------------------------------------------
# Admission: policy types
# ---------------------------------------------------------------------------


RejectReason = Literal[
    "self_echo",
    "self_ids_unknown",
    "bots_disabled",
    "bot_not_mentioned",
    "group_policy_rejected",
]


def _is_bot_sender(sender: Any) -> bool:
    # receive_v1 docs say {user, bot}; accept "app" defensively.
    return getattr(sender, "sender_type", "") in {"bot", "app"}


def _sender_identity(sender: Any) -> frozenset:
    # Take any non-empty id variant — tenant sender_id_type decides which are populated.
    sid = getattr(sender, "sender_id", None)
    if sid is None:
        return frozenset()
    return frozenset(
        v for v in (
            getattr(sid, "open_id", None),
            getattr(sid, "user_id", None),
            getattr(sid, "union_id", None),
        )
        if v
    )


# ---------------------------------------------------------------------------
# Markdown rendering helpers
# ---------------------------------------------------------------------------


def _escape_markdown_text(text: str) -> str:
    return _MARKDOWN_SPECIAL_CHARS_RE.sub(r"\\\1", text)


def _to_boolean(value: Any) -> bool:
    return value is True or value == 1 or value == "true"


def _env_boolean_default_true(name: str) -> bool:
    """Parse a user-facing boolean environment flag with a safe-on default."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return True
    normalized = raw.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    logger.warning("[Feishu] Invalid %s=%r; defaulting to true", name, raw)
    return True


def _cardkit_module() -> Any:
    """Return the card-output engine module (:mod:`feishu_cardkit`).

    Imported through a helper rather than a top-level ``from .`` because
    the plugin-adapter test loader execs this file by path under a
    package-less module name, where relative imports do not resolve.
    Falling back to a by-path load also avoids re-entering the package
    ``__init__``, which would import a second copy of this module.
    """
    global _card_output_module
    if _card_output_module is not None:
        return _card_output_module
    try:
        from . import feishu_cardkit as module  # type: ignore[import-not-found]
    except ImportError:
        import importlib.util
        import sys as _sys

        path = Path(__file__).with_name("feishu_cardkit.py")
        spec = importlib.util.spec_from_file_location("hermes_feishu_cardkit", str(path))
        if spec is None or spec.loader is None:  # pragma: no cover — defensive
            raise ImportError(f"Could not load {path}")
        module = importlib.util.module_from_spec(spec)
        _sys.modules.setdefault(spec.name, module)
        spec.loader.exec_module(module)
    _card_output_module = module
    return module


def _card_copy_from_env() -> Any:
    """Build the card's user-visible strings, honouring env overrides.

    Deployments localize or rebrand the card without touching code:
    ``FEISHU_CARD_TITLE``, ``FEISHU_CARD_DELEGATE_TITLE`` (may contain
    ``{agent}``), ``FEISHU_CARD_TRACE_TITLE`` and
    ``FEISHU_CARD_ATTACHMENT_NOTE`` (may contain ``{names}``).
    """
    defaults = _cardkit_module().CardCopy()
    overrides: Dict[str, Any] = {}
    for env_name, field_name in (
        ("FEISHU_CARD_TITLE", "main_title"),
        ("FEISHU_CARD_DELEGATE_TITLE", "delegate_title"),
        ("FEISHU_CARD_TRACE_TITLE", "trace_title"),
        ("FEISHU_CARD_ATTACHMENT_NOTE", "attachment_note"),
    ):
        raw = os.getenv(env_name, "").strip()
        if raw:
            overrides[field_name] = raw
    if not overrides:
        return defaults
    from dataclasses import replace as _dc_replace

    return _dc_replace(defaults, **overrides)


def _env_boolean_default_false(name: str) -> bool:
    """Parse a user-facing boolean environment flag that is off by default.

    Mirrors :func:`_env_boolean_default_true` so an operator can spell the
    value any of the usual ways.  Not expressible with ``_to_boolean``, which
    only accepts the literal string ``"true"`` — ``FEISHU_CARD_OUTPUT=1``
    would silently read as off.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return False
    normalized = raw.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    logger.warning("[Feishu] Invalid %s=%r; defaulting to false", name, raw)
    return False


def _is_style_enabled(style: Dict[str, Any] | None, key: str) -> bool:
    if not style:
        return False
    return _to_boolean(style.get(key))


def _wrap_inline_code(text: str) -> str:
    max_run = max([0, *[len(run) for run in re.findall(r"`+", text)]])
    fence = "`" * (max_run + 1)
    body = f" {text} " if text.startswith("`") or text.endswith("`") else text
    return f"{fence}{body}{fence}"


def _sanitize_fence_language(language: str) -> str:
    return language.strip().replace("\n", " ").replace("\r", " ")


def _render_text_element(element: Dict[str, Any]) -> str:
    text = str(element.get("text", "") or "")
    style = element.get("style")
    style_dict = style if isinstance(style, dict) else None

    if _is_style_enabled(style_dict, "code"):
        return _wrap_inline_code(text)

    rendered = _escape_markdown_text(text)
    if not rendered:
        return ""
    if _is_style_enabled(style_dict, "bold"):
        rendered = f"**{rendered}**"
    if _is_style_enabled(style_dict, "italic"):
        rendered = f"*{rendered}*"
    if _is_style_enabled(style_dict, "underline"):
        rendered = f"<u>{rendered}</u>"
    if _is_style_enabled(style_dict, "strikethrough"):
        rendered = f"~~{rendered}~~"
    return rendered


def _render_code_block_element(element: Dict[str, Any]) -> str:
    language = _sanitize_fence_language(
        str(element.get("language", "") or "") or str(element.get("lang", "") or "")
    )
    code = (
        str(element.get("text", "") or "") or str(element.get("content", "") or "")
    ).replace("\r\n", "\n")
    trailing_newline = "" if code.endswith("\n") else "\n"
    return f"```{language}\n{code}{trailing_newline}```"


def _strip_markdown_to_plain_text(text: str) -> str:
    """Strip markdown formatting to plain text for Feishu text fallbacks.

    Delegates common markdown stripping to the shared helper and adds
    Feishu-specific patterns (blockquotes, strikethrough, underline tags,
    horizontal rules, \\r\\n normalisation).
    """
    from gateway.platforms.helpers import strip_markdown
    plain = text.replace("\r\n", "\n")
    plain = _MARKDOWN_LINK_RE.sub(lambda m: f"{m.group(1)} ({m.group(2).strip()})", plain)
    plain = re.sub(r"^>\s?", "", plain, flags=re.MULTILINE)
    plain = re.sub(r"^\s*---+\s*$", "---", plain, flags=re.MULTILINE)
    plain = re.sub(r"~~([^~\n]+)~~", r"\1", plain)
    plain = re.sub(r"<u>([\s\S]*?)</u>", r"\1", plain)
    plain = strip_markdown(plain)
    return plain


def _coerce_int(value: Any, default: Optional[int] = None, min_value: int = 0) -> Optional[int]:
    """Coerce value to int with optional default and minimum constraint."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= min_value else default


def _coerce_required_int(value: Any, default: int, min_value: int = 0) -> int:
    parsed = _coerce_int(value, default=default, min_value=min_value)
    return default if parsed is None else parsed


# ---------------------------------------------------------------------------
# Post payload builders and parsers
# ---------------------------------------------------------------------------


def _build_markdown_post_payload(content: str) -> str:
    rows = _build_markdown_post_rows(content)
    return json.dumps(
        {
            "zh_cn": {
                "content": rows,
            }
        },
        ensure_ascii=False,
    )


def _build_markdown_post_rows(content: str) -> List[List[Dict[str, str]]]:
    """Build Feishu post rows while isolating fenced code blocks.

    Feishu's `md` renderer can swallow trailing content when a fenced code block
    appears inside one large markdown element. Split the reply at real fence
    lines so prose before/after the code block remains visible while code stays
    in a dedicated row.
    """
    if not content:
        return [[{"tag": "md", "text": ""}]]
    if "```" not in content:
        return [[{"tag": "md", "text": content}]]

    rows: List[List[Dict[str, str]]] = []
    current: List[str] = []
    in_code_block = False

    def _flush_current() -> None:
        nonlocal current
        if not current:
            return
        segment = "\n".join(current)
        if segment.strip():
            rows.append([{"tag": "md", "text": segment}])
        current = []

    for raw_line in content.splitlines():
        stripped_line = raw_line.strip()
        is_fence = bool(
            _MARKDOWN_FENCE_CLOSE_RE.match(stripped_line)
            if in_code_block
            else _MARKDOWN_FENCE_OPEN_RE.match(stripped_line)
        )

        if is_fence:
            if not in_code_block:
                _flush_current()
            current.append(raw_line)
            in_code_block = not in_code_block
            if not in_code_block:
                _flush_current()
            continue

        current.append(raw_line)

    _flush_current()
    return rows or [[{"tag": "md", "text": content}]]


def parse_feishu_post_payload(
    payload: Any,
    *,
    mentions_map: Optional[Dict[str, FeishuMentionRef]] = None,
) -> FeishuPostParseResult:
    resolved = _resolve_post_payload(payload)
    if not resolved:
        return FeishuPostParseResult(text_content=FALLBACK_POST_TEXT)

    image_keys: List[str] = []
    media_refs: List[FeishuPostMediaRef] = []
    parts: List[str] = []

    title = _normalize_feishu_text(str(resolved.get("title", "")).strip())
    if title:
        parts.append(title)

    for row in resolved.get("content", []) or []:
        if not isinstance(row, list):
            continue
        row_text = _normalize_feishu_text(
            "".join(
                _render_post_element(item, image_keys, media_refs, mentions_map)
                for item in row
            )
        )
        if row_text:
            parts.append(row_text)

    return FeishuPostParseResult(
        text_content="\n".join(parts).strip() or FALLBACK_POST_TEXT,
        image_keys=image_keys,
        media_refs=media_refs,
    )


def _resolve_post_payload(payload: Any) -> Dict[str, Any]:
    direct = _to_post_payload(payload)
    if direct:
        return direct
    if not isinstance(payload, dict):
        return {}

    wrapped = payload.get("post")
    wrapped_direct = _resolve_locale_payload(wrapped)
    if wrapped_direct:
        return wrapped_direct
    return _resolve_locale_payload(payload)


def _resolve_locale_payload(payload: Any) -> Dict[str, Any]:
    direct = _to_post_payload(payload)
    if direct:
        return direct
    if not isinstance(payload, dict):
        return {}

    for key in _PREFERRED_LOCALES:
        candidate = _to_post_payload(payload.get(key))
        if candidate:
            return candidate
    for value in payload.values():
        candidate = _to_post_payload(value)
        if candidate:
            return candidate
    return {}


def _to_post_payload(candidate: Any) -> Dict[str, Any]:
    if not isinstance(candidate, dict):
        return {}
    content = candidate.get("content")
    if not isinstance(content, list):
        return {}
    return {
        "title": str(candidate.get("title", "") or ""),
        "content": content,
    }


def _render_post_element(
    element: Any,
    image_keys: List[str],
    media_refs: List[FeishuPostMediaRef],
    mentions_map: Optional[Dict[str, FeishuMentionRef]] = None,
) -> str:
    if isinstance(element, str):
        return element
    if not isinstance(element, dict):
        return ""

    tag = str(element.get("tag", "")).strip().lower()
    if tag == "text":
        return _render_text_element(element)
    if tag == "a":
        href = str(element.get("href", "")).strip()
        label = str(element.get("text", href) or "").strip()
        if not label:
            return ""
        escaped_label = _escape_markdown_text(label)
        return f"[{escaped_label}]({href})" if href else escaped_label
    if tag == "at":
        # Post <at>.user_id is a placeholder ("@_user_N" or "@_all"); look up
        # the real ref in mentions_map for the display name.
        placeholder = str(element.get("user_id", "")).strip()
        if placeholder == "@_all":
            # Feishu SDK sometimes omits @_all from the top-level mentions
            # payload; record it here so the caller's mention list stays complete.
            if mentions_map is not None and "@_all" not in mentions_map:
                mentions_map["@_all"] = FeishuMentionRef(is_all=True)
            return "@all"
        ref = (mentions_map or {}).get(placeholder)
        if ref is not None:
            display_name = ref.name or ref.open_id or "user"
        else:
            display_name = str(element.get("user_name", "")).strip() or "user"
        return f"@{_escape_markdown_text(display_name)}"
    if tag in {"img", "image"}:
        image_key = str(element.get("image_key", "")).strip()
        if image_key and image_key not in image_keys:
            image_keys.append(image_key)
        alt = str(element.get("text", "")).strip() or str(element.get("alt", "")).strip()
        return f"[Image: {alt}]" if alt else "[Image]"
    if tag in {"media", "file", "audio", "video"}:
        file_key = str(element.get("file_key", "")).strip()
        file_name = (
            str(element.get("file_name", "")).strip()
            or str(element.get("title", "")).strip()
            or str(element.get("text", "")).strip()
        )
        if file_key:
            media_refs.append(
                FeishuPostMediaRef(
                    file_key=file_key,
                    file_name=file_name,
                    resource_type=tag if tag in {"audio", "video"} else "file",
                )
            )
        return f"[Attachment: {file_name}]" if file_name else "[Attachment]"
    if tag in {"emotion", "emoji"}:
        label = str(element.get("text", "")).strip() or str(element.get("emoji_type", "")).strip()
        return f":{_escape_markdown_text(label)}:" if label else "[Emoji]"
    if tag == "br":
        return "\n"
    if tag in {"hr", "divider"}:
        return "\n\n---\n\n"
    if tag == "code":
        code = str(element.get("text", "") or "") or str(element.get("content", "") or "")
        return _wrap_inline_code(code) if code else ""
    if tag in {"code_block", "pre"}:
        return _render_code_block_element(element)

    nested_parts: List[str] = []
    for key in ("text", "title", "content", "children", "elements"):
        extracted = _render_nested_post(element.get(key), image_keys, media_refs, mentions_map)
        if extracted:
            nested_parts.append(extracted)
    return " ".join(part for part in nested_parts if part)


def _render_nested_post(
    value: Any,
    image_keys: List[str],
    media_refs: List[FeishuPostMediaRef],
    mentions_map: Optional[Dict[str, FeishuMentionRef]] = None,
) -> str:
    if isinstance(value, str):
        return _escape_markdown_text(value)
    if isinstance(value, list):
        return " ".join(
            part
            for item in value
            for part in [_render_nested_post(item, image_keys, media_refs, mentions_map)]
            if part
        )
    if isinstance(value, dict):
        direct = _render_post_element(value, image_keys, media_refs, mentions_map)
        if direct:
            return direct
        return " ".join(
            part
            for item in value.values()
            for part in [_render_nested_post(item, image_keys, media_refs, mentions_map)]
            if part
        )
    return ""


# ---------------------------------------------------------------------------
# Message normalization
# ---------------------------------------------------------------------------


def normalize_feishu_message(
    *,
    message_type: str,
    raw_content: str,
    mentions: Optional[Sequence[Any]] = None,
    bot: _FeishuBotIdentity = _FeishuBotIdentity(),
) -> FeishuNormalizedMessage:
    normalized_type = str(message_type or "").strip().lower()
    payload = _load_feishu_payload(raw_content)
    mentions_map = _build_mentions_map(mentions, bot)

    if normalized_type == "text":
        text = str(payload.get("text", "") or "")
        # Feishu SDK sometimes omits @_all from the mentions payload even when
        # the text literal contains it (confirmed via im.v1.message.get).
        if "@_all" in text and "@_all" not in mentions_map:
            mentions_map["@_all"] = FeishuMentionRef(is_all=True)
        return FeishuNormalizedMessage(
            raw_type=normalized_type,
            text_content=_normalize_feishu_text(text, mentions_map),
            mentions=list(mentions_map.values()),
        )
    if normalized_type == "post":
        # The walker writes back to mentions_map if it encounters
        # <at user_id="@_all">, so reading .values() after parsing is enough.
        parsed_post = parse_feishu_post_payload(payload, mentions_map=mentions_map)
        return FeishuNormalizedMessage(
            raw_type=normalized_type,
            text_content=parsed_post.text_content,
            image_keys=list(parsed_post.image_keys),
            media_refs=list(parsed_post.media_refs),
            mentions=list(mentions_map.values()),
            relation_kind="post",
        )
    mention_refs = list(mentions_map.values())
    if normalized_type == "image":
        image_key = str(payload.get("image_key", "") or "").strip()
        alt_text = _normalize_feishu_text(
            str(payload.get("text", "") or "")
            or str(payload.get("alt", "") or "")
            or FALLBACK_IMAGE_TEXT,
            mentions_map,
        )
        return FeishuNormalizedMessage(
            raw_type=normalized_type,
            text_content=alt_text if alt_text != FALLBACK_IMAGE_TEXT else "",
            preferred_message_type="photo",
            image_keys=[image_key] if image_key else [],
            relation_kind="image",
            mentions=mention_refs,
        )
    if normalized_type in {"file", "audio", "media"}:
        media_ref = _build_media_ref_from_payload(payload, resource_type=normalized_type)
        placeholder = _attachment_placeholder(media_ref.file_name)
        return FeishuNormalizedMessage(
            raw_type=normalized_type,
            text_content="",
            preferred_message_type="audio" if normalized_type == "audio" else "document",
            media_refs=[media_ref] if media_ref.file_key else [],
            relation_kind=normalized_type,
            metadata={"placeholder_text": placeholder},
            mentions=mention_refs,
        )
    if normalized_type == "merge_forward":
        return _normalize_merge_forward_message(payload)
    if normalized_type == "share_chat":
        return _normalize_share_chat_message(payload)
    if normalized_type in {"interactive", "card"}:
        return _normalize_interactive_message(normalized_type, payload)

    return FeishuNormalizedMessage(raw_type=normalized_type, text_content="")


def _load_feishu_payload(raw_content: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw_content) if raw_content else {}
    except json.JSONDecodeError:
        return {"text": raw_content}
    return parsed if isinstance(parsed, dict) else {"content": parsed}


def _normalize_merge_forward_message(payload: Dict[str, Any]) -> FeishuNormalizedMessage:
    title = _first_non_empty_text(
        payload.get("title"),
        payload.get("summary"),
        payload.get("preview"),
        _find_first_text(payload, keys=("title", "summary", "preview", "description")),
    )
    entries = _collect_forward_entries(payload)
    lines: List[str] = []
    if title:
        lines.append(title)
    lines.extend(entries[:8])
    text_content = "\n".join(lines).strip() or FALLBACK_FORWARD_TEXT
    return FeishuNormalizedMessage(
        raw_type="merge_forward",
        text_content=text_content,
        relation_kind="merge_forward",
        metadata={"entry_count": len(entries), "title": title},
    )


def _normalize_share_chat_message(payload: Dict[str, Any]) -> FeishuNormalizedMessage:
    chat_name = _first_non_empty_text(
        payload.get("chat_name"),
        payload.get("name"),
        payload.get("title"),
        _find_first_text(payload, keys=("chat_name", "name", "title")),
    )
    share_id = _first_non_empty_text(
        payload.get("chat_id"),
        payload.get("open_chat_id"),
        payload.get("share_chat_id"),
    )
    lines = []
    if chat_name:
        lines.append(f"Shared chat: {chat_name}")
    else:
        lines.append(FALLBACK_SHARE_CHAT_TEXT)
    if share_id:
        lines.append(f"Chat ID: {share_id}")
    text_content = "\n".join(lines)
    return FeishuNormalizedMessage(
        raw_type="share_chat",
        text_content=text_content,
        relation_kind="share_chat",
        metadata={"chat_id": share_id, "chat_name": chat_name},
    )


def _normalize_interactive_message(message_type: str, payload: Dict[str, Any]) -> FeishuNormalizedMessage:
    card_payload = payload.get("card") if isinstance(payload.get("card"), dict) else payload
    title = _first_non_empty_text(
        _find_header_title(card_payload),
        payload.get("title"),
        _find_first_text(card_payload, keys=("title", "summary", "subtitle")),
    )
    body_lines = _collect_card_lines(card_payload)
    actions = _collect_action_labels(card_payload)

    lines: List[str] = []
    if title:
        lines.append(title)
    for line in body_lines:
        if line != title:
            lines.append(line)
    if actions:
        lines.append(f"Actions: {', '.join(actions)}")

    text_content = "\n".join(lines[:12]).strip() or FALLBACK_INTERACTIVE_TEXT
    return FeishuNormalizedMessage(
        raw_type=message_type,
        text_content=text_content,
        relation_kind="interactive",
        metadata={"title": title, "actions": actions},
    )


# ---------------------------------------------------------------------------
# Content extraction utilities (card / forward / text walking)
# ---------------------------------------------------------------------------


def _collect_forward_entries(payload: Dict[str, Any]) -> List[str]:
    candidates: List[Any] = []
    for key in ("messages", "items", "message_list", "records", "content"):
        value = payload.get(key)
        if isinstance(value, list):
            candidates.extend(value)
    entries: List[str] = []
    for item in candidates:
        if not isinstance(item, dict):
            text = _normalize_feishu_text(str(item or ""))
            if text:
                entries.append(f"- {text}")
            continue
        sender = _first_non_empty_text(
            item.get("sender_name"),
            item.get("user_name"),
            item.get("sender"),
            item.get("name"),
        )
        nested_type = str(item.get("message_type", "") or item.get("msg_type", "")).strip().lower()
        if nested_type == "post":
            body = parse_feishu_post_payload(item.get("content") or item).text_content
        else:
            body = _first_non_empty_text(
                item.get("text"),
                item.get("summary"),
                item.get("preview"),
                item.get("content"),
                _find_first_text(item, keys=("text", "content", "summary", "preview", "title")),
            )
        body = _normalize_feishu_text(body)
        if sender and body:
            entries.append(f"- {sender}: {body}")
        elif body:
            entries.append(f"- {body}")
    return _unique_lines(entries)


def _collect_card_lines(payload: Any) -> List[str]:
    lines = _collect_text_segments(payload, in_rich_block=False)
    normalized = [_normalize_feishu_text(line) for line in lines]
    return _unique_lines([line for line in normalized if line])


def _collect_action_labels(payload: Any) -> List[str]:
    labels: List[str] = []
    for item in _walk_nodes(payload):
        if not isinstance(item, dict):
            continue
        tag = str(item.get("tag", "") or item.get("type", "")).strip().lower()
        if tag not in {"button", "select_static", "overflow", "date_picker", "picker"}:
            continue
        label = _first_non_empty_text(
            item.get("text"),
            item.get("name"),
            item.get("value"),
            _find_first_text(item, keys=("text", "content", "name", "value")),
        )
        if label:
            labels.append(label)
    return _unique_lines(labels)


def _collect_text_segments(value: Any, *, in_rich_block: bool) -> List[str]:
    if isinstance(value, str):
        return [_normalize_feishu_text(value)] if in_rich_block else []
    if isinstance(value, list):
        segments: List[str] = []
        for item in value:
            segments.extend(_collect_text_segments(item, in_rich_block=in_rich_block))
        return segments
    if not isinstance(value, dict):
        return []

    tag = str(value.get("tag", "") or value.get("type", "")).strip().lower()
    next_in_rich_block = in_rich_block or tag in {
        "plain_text",
        "lark_md",
        "markdown",
        "note",
        "div",
        "column_set",
        "column",
        "action",
        "button",
        "select_static",
        "date_picker",
    }

    segments: List[str] = []
    for key in _SUPPORTED_CARD_TEXT_KEYS:
        item = value.get(key)
        if isinstance(item, str) and next_in_rich_block:
            normalized = _normalize_feishu_text(item)
            if normalized:
                segments.append(normalized)

    for key, item in value.items():
        if key in _SKIP_TEXT_KEYS:
            continue
        segments.extend(_collect_text_segments(item, in_rich_block=next_in_rich_block))
    return segments


def _build_media_ref_from_payload(payload: Dict[str, Any], *, resource_type: str) -> FeishuPostMediaRef:
    file_key = str(payload.get("file_key", "") or "").strip()
    file_name = _first_non_empty_text(
        payload.get("file_name"),
        payload.get("title"),
        payload.get("text"),
    )
    effective_type = resource_type if resource_type in {"audio", "video"} else "file"
    return FeishuPostMediaRef(file_key=file_key, file_name=file_name, resource_type=effective_type)


def _attachment_placeholder(file_name: str) -> str:
    normalized_name = _normalize_feishu_text(file_name)
    return f"[Attachment: {normalized_name}]" if normalized_name else FALLBACK_ATTACHMENT_TEXT


def _find_header_title(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    header = payload.get("header")
    if not isinstance(header, dict):
        return ""
    title = header.get("title")
    if isinstance(title, dict):
        return _first_non_empty_text(title.get("content"), title.get("text"), title.get("name"))
    return _normalize_feishu_text(str(title or ""))


def _find_first_text(payload: Any, *, keys: tuple[str, ...]) -> str:
    for node in _walk_nodes(payload):
        if not isinstance(node, dict):
            continue
        for key in keys:
            value = node.get(key)
            if isinstance(value, str):
                normalized = _normalize_feishu_text(value)
                if normalized:
                    return normalized
    return ""


def _walk_nodes(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk_nodes(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_nodes(item)


def _first_non_empty_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str):
            normalized = _normalize_feishu_text(value)
            if normalized:
                return normalized
        elif value is not None and not isinstance(value, (dict, list)):
            normalized = _normalize_feishu_text(str(value))
            if normalized:
                return normalized
    return ""


# ---------------------------------------------------------------------------
# General text utilities
# ---------------------------------------------------------------------------


def _normalize_feishu_text(
    text: str,
    mentions_map: Optional[Dict[str, FeishuMentionRef]] = None,
) -> str:
    def _sub(match: "re.Match[str]") -> str:
        key = match.group(0)
        ref = (mentions_map or {}).get(key)
        if ref is None:
            return " "
        name = ref.name or ref.open_id or "user"
        return f"@{name}"

    cleaned = _MENTION_PLACEHOLDER_RE.sub(_sub, text or "")
    cleaned = cleaned.replace("@_all", "@all")
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = "\n".join(_WHITESPACE_RE.sub(" ", line).strip() for line in cleaned.split("\n"))
    cleaned = "\n".join(line for line in cleaned.split("\n") if line)
    cleaned = _MULTISPACE_RE.sub(" ", cleaned)
    return cleaned.strip()


def _unique_lines(lines: List[str]) -> List[str]:
    seen: set[str] = set()
    unique: List[str] = []
    for line in lines:
        if not line or line in seen:
            continue
        seen.add(line)
        unique.append(line)
    return unique


# ---------------------------------------------------------------------------
# Mention helpers
# ---------------------------------------------------------------------------


def _extract_mention_ids(mention: Any) -> tuple[str, str]:
    # Returns (open_id, user_id). im.v1.message.get hands back id as a string
    # plus id_type discriminator; event payloads hand back a nested UserId
    # object carrying both fields.
    mention_id = getattr(mention, "id", None)
    if isinstance(mention_id, str):
        id_type = str(getattr(mention, "id_type", "") or "").lower()
        if id_type == "open_id":
            return mention_id, ""
        if id_type == "user_id":
            return "", mention_id
        return "", ""
    if mention_id is None:
        return "", ""
    return (
        str(getattr(mention_id, "open_id", "") or ""),
        str(getattr(mention_id, "user_id", "") or ""),
    )


def _build_mentions_map(
    mentions: Optional[Sequence[Any]],
    bot: _FeishuBotIdentity,
) -> Dict[str, FeishuMentionRef]:
    result: Dict[str, FeishuMentionRef] = {}
    for mention in mentions or []:
        key = str(getattr(mention, "key", "") or "")
        if not key:
            continue
        if key == "@_all":
            result[key] = FeishuMentionRef(is_all=True)
            continue
        open_id, user_id = _extract_mention_ids(mention)
        name = str(getattr(mention, "name", "") or "").strip()
        result[key] = FeishuMentionRef(
            name=name,
            open_id=open_id,
            is_self=bot.matches(open_id=open_id, user_id=user_id, name=name),
        )
    return result


def _build_mention_hint(mentions: Sequence[FeishuMentionRef]) -> str:
    parts: List[str] = []
    seen: set = set()
    for ref in mentions:
        if ref.is_self:
            continue
        signature = (ref.is_all, ref.open_id, ref.name)
        if signature in seen:
            continue
        seen.add(signature)
        if ref.is_all:
            parts.append("@all")
        elif ref.open_id:
            parts.append(f"{ref.name or 'unknown'} (open_id={ref.open_id})")
        else:
            parts.append(ref.name or "unknown")
    return f"[Mentioned: {', '.join(parts)}]" if parts else ""


def _strip_edge_self_mentions(
    text: str,
    mentions: Sequence[FeishuMentionRef],
) -> str:
    # Leading: strip consecutive self-mentions unconditionally.
    # Trailing: strip only when followed by whitespace/terminal punct, so
    # mid-sentence references ("don't @Bot again") stay intact.
    # Leading word-boundary prevents @Al from eating @Alice.
    if not text:
        return text
    self_names = [
        f"@{ref.name or ref.open_id or 'user'}"
        for ref in mentions
        if ref.is_self
    ]
    if not self_names:
        return text

    remaining = text.lstrip()
    while True:
        for nm in self_names:
            if not remaining.startswith(nm):
                continue
            after = remaining[len(nm):]
            if after and after[0] not in _MENTION_BOUNDARY_CHARS:
                continue
            remaining = after.lstrip()
            break
        else:
            break

    while True:
        i = len(remaining)
        while i > 0 and remaining[i - 1] in _TRAILING_TERMINAL_PUNCT:
            i -= 1
        body = remaining[:i]
        tail = remaining[i:]
        for nm in self_names:
            if body.endswith(nm):
                remaining = body[: -len(nm)].rstrip() + tail
                break
        else:
            return remaining


def _run_official_feishu_ws_client(ws_client: Any, adapter: Any) -> None:
    """Run the official Lark WS client in its own thread-local event loop."""
    import lark_oapi.ws.client as ws_client_module

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    ws_client_module.loop = loop
    adapter._ws_thread_loop = loop

    original_connect = ws_client_module.websockets.connect
    original_configure = getattr(ws_client, "_configure", None)

    def _apply_runtime_ws_overrides() -> None:
        try:
            setattr(ws_client, "_reconnect_nonce", adapter._ws_reconnect_nonce)
            setattr(ws_client, "_reconnect_interval", adapter._ws_reconnect_interval)
            if adapter._ws_ping_interval is not None:
                setattr(ws_client, "_ping_interval", adapter._ws_ping_interval)
        except Exception:
            logger.debug("[Feishu] Failed to apply websocket runtime overrides", exc_info=True)

    def _connect_with_overrides(*args: Any, **kwargs: Any) -> Any:
        if adapter._ws_ping_interval is not None and "ping_interval" not in kwargs:
            kwargs["ping_interval"] = adapter._ws_ping_interval
        if adapter._ws_ping_timeout is not None and "ping_timeout" not in kwargs:
            kwargs["ping_timeout"] = adapter._ws_ping_timeout
        return original_connect(*args, **kwargs)

    def _configure_with_overrides(conf: Any) -> Any:
        if original_configure is None:
            raise RuntimeError("Feishu _configure_with_overrides called but original_configure is None")
        result = original_configure(conf)
        _apply_runtime_ws_overrides()
        return result

    ws_client_module.websockets.connect = _connect_with_overrides
    if original_configure is not None:
        setattr(ws_client, "_configure", _configure_with_overrides)
    _apply_runtime_ws_overrides()
    try:
        ws_client.start()
    except Exception:
        pass
    finally:
        ws_client_module.websockets.connect = original_connect
        if original_configure is not None:
            setattr(ws_client, "_configure", original_configure)
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        try:
            loop.stop()
        except Exception:
            pass
        try:
            loop.close()
        except Exception:
            pass
        adapter._ws_thread_loop = None


def _load_lark_oapi() -> bool:
    """Import and bind the Feishu SDK after an explicit connection request."""
    if FEISHU_AVAILABLE:
        return True

    with _lark_import_lock:
        if FEISHU_AVAILABLE:
            return True
        try:
            import lark_oapi as lark
            from lark_oapi.api.application.v6 import GetApplicationRequest
            from lark_oapi.api.im.v1 import (
                CreateFileRequest, CreateFileRequestBody,
                CreateImageRequest, CreateImageRequestBody,
                CreateMessageRequest, CreateMessageRequestBody,
                GetChatRequest, GetMessageRequest, GetMessageResourceRequest,
                P2ImMessageMessageReadV1,
                ReplyMessageRequest, ReplyMessageRequestBody,
                UpdateMessageRequest, UpdateMessageRequestBody,
            )
            from lark_oapi.core import AccessTokenType, HttpMethod
            from lark_oapi.core.const import FEISHU_DOMAIN, LARK_DOMAIN
            from lark_oapi.core.model import BaseRequest
            from lark_oapi.event.callback.model.p2_card_action_trigger import (
                CallBackCard, P2CardActionTriggerResponse,
            )
            from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
            from lark_oapi.ws import Client as FeishuWSClient
        except ImportError:
            return False

        globals().update({
            "lark": lark,
            "GetApplicationRequest": GetApplicationRequest,
            "CreateFileRequest": CreateFileRequest,
            "CreateFileRequestBody": CreateFileRequestBody,
            "CreateImageRequest": CreateImageRequest,
            "CreateImageRequestBody": CreateImageRequestBody,
            "CreateMessageRequest": CreateMessageRequest,
            "CreateMessageRequestBody": CreateMessageRequestBody,
            "GetChatRequest": GetChatRequest,
            "GetMessageRequest": GetMessageRequest,
            "GetMessageResourceRequest": GetMessageResourceRequest,
            "P2ImMessageMessageReadV1": P2ImMessageMessageReadV1,
            "ReplyMessageRequest": ReplyMessageRequest,
            "ReplyMessageRequestBody": ReplyMessageRequestBody,
            "UpdateMessageRequest": UpdateMessageRequest,
            "UpdateMessageRequestBody": UpdateMessageRequestBody,
            "AccessTokenType": AccessTokenType,
            "HttpMethod": HttpMethod,
            "FEISHU_DOMAIN": FEISHU_DOMAIN,
            "LARK_DOMAIN": LARK_DOMAIN,
            "BaseRequest": BaseRequest,
            "CallBackCard": CallBackCard,
            "P2CardActionTriggerResponse": P2CardActionTriggerResponse,
            "EventDispatcherHandler": EventDispatcherHandler,
            "FeishuWSClient": FeishuWSClient,
            "FEISHU_AVAILABLE": True,
        })
        return True


def feishu_deps_present() -> bool:
    """PASSIVE probe: is lark-oapi installed right now?

    Registry ``check_fn`` — called from status displays and config loading,
    so it must never install anything.  Uses ``is_available`` (cheap
    importlib.metadata lookups) instead of importing the SDK, which is
    deferred to ``_load_lark_oapi`` at connect time.  The ACTIVE
    lazy-installer (``check_feishu_requirements``) is registered as
    ``ensure_deps_fn`` and runs from ``create_adapter()`` when this
    returns False (#79812).
    """
    if FEISHU_AVAILABLE:
        return True
    try:
        from tools.lazy_deps import is_available
        return is_available("platform.feishu")
    except Exception:  # pragma: no cover — defensive
        return False


def check_feishu_requirements() -> bool:
    """Ensure Feishu dependencies are installed without importing the SDK."""
    if FEISHU_AVAILABLE:
        return True

    from tools.lazy_deps import ensure

    try:
        ensure("platform.feishu", prompt=False)
        return True
    except Exception:
        return False


class FeishuAdapter(BasePlatformAdapter):
    """Feishu/Lark bot adapter."""

    supports_code_blocks = True  # Feishu renders fenced code blocks
    splits_long_messages = True  # send() chunks via truncate_message(MAX_MESSAGE_LENGTH)

    MAX_MESSAGE_LENGTH = 8000
    # Max distinct chat IDs retained in _chat_locks before LRU eviction kicks in.
    CHAT_LOCK_MAX_SIZE: int = 1000
    # Threshold for detecting Feishu client-side message splits.
    # When a chunk is near the ~4096-char practical limit, a continuation
    # is almost certain.
    _SPLIT_THRESHOLD = 4000

    # =========================================================================
    # Lifecycle — init / settings / connect / disconnect
    # =========================================================================

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.FEISHU)

        self._settings = self._load_settings(config.extra or {})
        self._apply_settings(self._settings)
        self._client: Optional[Any] = None
        # Adapter-owned thread pool for blocking Feishu SDK calls. Routing SDK
        # work through this pool (instead of asyncio's shared default executor)
        # means a torn-down default executor can no longer wedge sends with
        # "Executor shutdown has been called" — the pool is recreated on demand
        # if it has been shut down. See issue #10849.
        self._sdk_executor_lock = threading.Lock()
        self._sdk_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
        # Set on disconnect/shutdown so a real teardown can't be resurrected
        # by the recreate-on-shutdown path; cleared on connect for reconnects.
        self._sdk_executor_closing = False
        self._ws_client: Optional[Any] = None
        self._ws_future: Optional[asyncio.Future] = None
        self._ws_thread_loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._webhook_runner: Optional[Any] = None
        self._webhook_site: Optional[Any] = None
        self._event_handler: Optional[Any] = None
        self._seen_message_ids: Dict[str, float] = {}  # message_id → seen_at (time.time())
        self._seen_message_order: List[str] = []
        self._dedup_state_path = get_hermes_home() / "feishu_seen_message_ids.json"
        self._dedup_lock = threading.Lock()
        self._sender_name_cache: Dict[str, tuple[str, float]] = {}  # sender_id → (name, expire_at)
        self._webhook_rate_counts: Dict[str, tuple[int, float]] = {}  # rate_key → (count, window_start)
        self._webhook_anomaly_counts: Dict[str, tuple[int, str, float]] = {}  # ip → (count, last_status, first_seen)
        self._card_action_tokens: Dict[str, float] = {}  # token → first_seen_time
        # Inbound events that arrived before the adapter loop was ready
        # (e.g. during startup/restart or network-flap reconnect). A single
        # drainer thread replays them as soon as the loop becomes available.
        self._pending_inbound_events: List[Any] = []
        self._pending_inbound_lock = threading.Lock()
        self._pending_drain_scheduled = False
        self._pending_inbound_max_depth = 1000  # cap queue; drop oldest beyond
        self._chat_locks: "collections.OrderedDict[str, asyncio.Lock]" = collections.OrderedDict()  # chat_id → lock (per-chat serial processing, LRU-bounded)
        self._sent_message_ids_to_chat: Dict[str, str] = {}  # message_id → chat_id (for reaction routing)
        self._sent_message_id_order: List[str] = []  # LRU order for _sent_message_ids_to_chat
        self._chat_info_cache: Dict[str, Dict[str, Any]] = {}
        self._message_text_cache: "OrderedDict[str, Optional[str]]" = OrderedDict()
        self._app_lock_identity: Optional[str] = None
        self._text_batch_state = FeishuBatchState()
        self._pending_text_batches = self._text_batch_state.events
        self._pending_text_batch_tasks = self._text_batch_state.tasks
        self._pending_text_batch_counts = self._text_batch_state.counts
        self._media_batch_state = FeishuBatchState()
        self._pending_media_batches = self._media_batch_state.events
        self._pending_media_batch_tasks = self._media_batch_state.tasks
        # Exec approval button state (approval_id → {session_key, message_id, chat_id})
        self._approval_state: Dict[int, Dict[str, str]] = {}
        self._approval_counter = itertools.count(1)
        # Update prompt button state (prompt_id → {session_key, message_id, chat_id})
        self._update_prompt_state: Dict[int, Dict[str, str]] = {}
        self._update_prompt_counter = itertools.count(1)
        # Clarify card state is adapter-local: card payloads carry only the
        # clarify id and option index, never the original choice text.
        self._clarify_choices: Dict[str, Dict[str, Any]] = {}
        # Remote A2A interaction cards are kept outside the local gateway
        # approval/clarify maps; their responder is an authenticated A2A
        # bridge owned by the delegate client.
        self._delegate_interactions: Dict[str, Dict[str, Any]] = {}
        self._DELEGATE_INTERACTIONS_MAX = 1000
        # Foreground A2A routes and stream state are process-local by design.
        self._delegate_routes: Dict[str, _FeishuDelegateRoute] = {}
        self._delegate_routes_lock = threading.RLock()
        self._delegate_stream_states: Dict[str, _FeishuDelegateStreamState] = {}
        self._delegate_loop: Optional[asyncio.AbstractEventLoop] = None
        # Feishu reaction deletion requires the opaque reaction_id returned
        # by create, so we cache it per message_id.
        self._pending_processing_reactions: "OrderedDict[str, str]" = OrderedDict()
        # A top-level message is a prospective thread only until its first
        # threaded reply succeeds. Failed roots stay flat for the remainder of
        # the turn so progress/final/error paths do not repeatedly retry topic
        # creation after the one-shot fallback.
        self._pending_auto_thread_roots: "OrderedDict[str, None]" = OrderedDict()
        self._failed_auto_thread_roots: "OrderedDict[str, None]" = OrderedDict()
        # Card output.  The manager owns one live card per route and seals
        # it whenever the speaker changes (main agent ↔ delegated remote
        # agent), which is what gives every a2a_delegate run its own card.
        self._card_copy = _card_copy_from_env()
        self._card_manager = _cardkit_module().FeishuCardOutputManager(
            self,
            copy=self._card_copy,
            flush_interval=env_float(
                "HERMES_FEISHU_CARD_FLUSH_INTERVAL", _DEFAULT_CARD_FLUSH_INTERVAL
            ),
        )
        # Per-delegate body accumulation: owner → {block_id, text}.  The
        # CardKit streaming endpoint takes the element's complete text on
        # every call, so the accumulated string is kept here.
        self._delegate_card_blocks: Dict[str, Dict[str, Any]] = {}
        # Delegate events arrive as independent fire-and-forget tasks, so the
        # "open a block or extend the existing one" decision needs a critical
        # section of its own — see handle_delegate_card_event.
        self._delegate_card_locks: Dict[str, asyncio.Lock] = {}
        # Owners that have streamed any body text during the current exchange.
        # Kept apart from _delegate_card_blocks, which a tool boundary clears:
        # "did this delegate stream?" is a property of the exchange, not of
        # the current text segment.
        self._delegate_card_streamed: set = set()
        # Files already uploaded during the current card turn, per route.  A
        # file can reach the uploader from two directions in one turn — the
        # gateway's own extraction and the card-text safety net below — and
        # the ledger is what keeps that from sending it twice.
        self._card_media_dispatched: Dict[str, "OrderedDict[str, None]"] = {}
        self._load_seen_message_ids()

    @staticmethod
    def _trim_oldest_dict_entries(mapping: Dict[Any, Any], max_size: int) -> None:
        while len(mapping) > max_size:
            mapping.pop(next(iter(mapping)), None)

    def _reply_thread_enabled(self) -> bool:
        configured = getattr(self, "_reply_thread_enabled_setting", None)
        if configured is not None:
            return bool(configured)
        return _env_boolean_default_true("FEISHU_REPLY_THREAD")

    @staticmethod
    def _is_message_thread_anchor(thread_id: Any) -> bool:
        """Return True when thread metadata carries a root message id.

        Feishu message ids use ``om_*`` while real thread ids use ``omt_*``.
        Auto-created lanes deliberately expose the former as ``source.thread_id``
        so the initiating turn and later replies can share one session key.
        """
        return str(thread_id or "").startswith("om_")

    def _auto_thread_state(self, name: str) -> "OrderedDict[str, None]":
        state = getattr(self, name, None)
        if state is None:
            state = OrderedDict()
            setattr(self, name, state)
        return state

    def _mark_auto_thread_pending(self, root_message_id: str) -> None:
        pending = self._auto_thread_state("_pending_auto_thread_roots")
        failed = self._auto_thread_state("_failed_auto_thread_roots")
        failed.pop(root_message_id, None)
        pending[root_message_id] = None
        pending.move_to_end(root_message_id)
        self._trim_oldest_dict_entries(pending, self.CHAT_LOCK_MAX_SIZE)

    def _mark_auto_thread_established(self, root_message_id: str) -> None:
        self._auto_thread_state("_pending_auto_thread_roots").pop(root_message_id, None)
        self._auto_thread_state("_failed_auto_thread_roots").pop(root_message_id, None)

    def _mark_auto_thread_failed(self, root_message_id: str) -> None:
        self._auto_thread_state("_pending_auto_thread_roots").pop(root_message_id, None)
        failed = self._auto_thread_state("_failed_auto_thread_roots")
        failed[root_message_id] = None
        failed.move_to_end(root_message_id)
        self._trim_oldest_dict_entries(failed, self.CHAT_LOCK_MAX_SIZE)

    def _session_exists_for_source(self, source: Any) -> bool:
        """Check active and persisted routing state for a candidate source."""
        try:
            from gateway.session import build_session_key

            extra = getattr(getattr(self, "config", None), "extra", None) or {}
            session_key = build_session_key(
                source,
                group_sessions_per_user=extra.get("group_sessions_per_user", True),
                thread_sessions_per_user=extra.get("thread_sessions_per_user", False),
                profile=getattr(source, "profile", None),
            )
        except Exception:
            logger.debug("[Feishu] Failed to build candidate auto-thread session key", exc_info=True)
            return False

        if session_key in (getattr(self, "_active_sessions", None) or {}):
            return True
        store = getattr(self, "_session_store", None)
        peek = getattr(store, "peek_session_id", None)
        if not callable(peek):
            return False
        try:
            return bool(peek(session_key))
        except Exception:
            logger.debug("[Feishu] Failed to inspect candidate auto-thread session", exc_info=True)
            return False

    @staticmethod
    def _load_settings(extra: Dict[str, Any]) -> FeishuAdapterSettings:
        # Parse per-group rules from config
        raw_group_rules = extra.get("group_rules", {})
        group_rules: Dict[str, FeishuGroupRule] = {}
        if isinstance(raw_group_rules, dict):
            for chat_id, rule_cfg in raw_group_rules.items():
                if not isinstance(rule_cfg, dict):
                    continue
                # Only override when the key is explicitly set — missing vs false
                # must not collapse.
                per_chat_require_mention: Optional[bool] = None
                if "require_mention" in rule_cfg:
                    per_chat_require_mention = _to_boolean(rule_cfg.get("require_mention"))
                group_rules[str(chat_id)] = FeishuGroupRule(
                    policy=str(rule_cfg.get("policy", "open")).strip().lower(),
                    allowlist={str(u).strip() for u in rule_cfg.get("allowlist", []) if str(u).strip()},
                    blacklist={str(u).strip() for u in rule_cfg.get("blacklist", []) if str(u).strip()},
                    require_mention=per_chat_require_mention,
                )

        # Bot-level admins
        raw_admins = extra.get("admins", [])
        admins = frozenset(str(u).strip() for u in raw_admins if str(u).strip())

        # Default group policy (for groups not in group_rules)
        default_group_policy = str(extra.get("default_group_policy", "")).strip().lower()

        # Env-only so adapter and gateway auth bypass share one source; yaml
        # feishu.allow_bots is bridged to this env var at config load.
        allow_bots = os.getenv("FEISHU_ALLOW_BOTS", "none").strip().lower()
        if allow_bots not in {"none", "mentions", "all"}:
            logger.warning(
                "[Feishu] Unknown allow_bots=%r, falling back to 'none'. Valid: none, mentions, all.",
                allow_bots,
            )
            allow_bots = "none"

        return FeishuAdapterSettings(
            app_id=str(extra.get("app_id") or os.getenv("FEISHU_APP_ID", "")).strip(),
            app_secret=str(extra.get("app_secret") or _get_scoped_secret("FEISHU_APP_SECRET", "")).strip(),
            domain_name=str(extra.get("domain") or os.getenv("FEISHU_DOMAIN", "feishu")).strip().lower(),
            connection_mode=str(
                extra.get("connection_mode") or os.getenv("FEISHU_CONNECTION_MODE", "websocket")
            ).strip().lower(),
            encrypt_key=str(extra.get("encrypt_key") or _get_scoped_secret("FEISHU_ENCRYPT_KEY", "")).strip(),
            verification_token=str(
                extra.get("verification_token") or _get_scoped_secret("FEISHU_VERIFICATION_TOKEN", "")
            ).strip(),
            group_policy=os.getenv("FEISHU_GROUP_POLICY", "allowlist").strip().lower(),
            allowed_group_users=frozenset(
                item.strip()
                for item in os.getenv("FEISHU_ALLOWED_USERS", "").split(",")
                if item.strip()
            ),
            bot_open_id=os.getenv("FEISHU_BOT_OPEN_ID", "").strip(),
            bot_user_id=os.getenv("FEISHU_BOT_USER_ID", "").strip(),
            bot_name=os.getenv("FEISHU_BOT_NAME", "").strip(),
            dedup_cache_size=max(
                32,
                env_int("HERMES_FEISHU_DEDUP_CACHE_SIZE", _DEFAULT_DEDUP_CACHE_SIZE),
            ),
            text_batch_delay_seconds=env_float(
                "HERMES_FEISHU_TEXT_BATCH_DELAY_SECONDS", _DEFAULT_TEXT_BATCH_DELAY_SECONDS
            ),
            text_batch_split_delay_seconds=env_float(
                "HERMES_FEISHU_TEXT_BATCH_SPLIT_DELAY_SECONDS", 2.0
            ),
            text_batch_max_messages=max(
                1,
                env_int("HERMES_FEISHU_TEXT_BATCH_MAX_MESSAGES", _DEFAULT_TEXT_BATCH_MAX_MESSAGES),
            ),
            text_batch_max_chars=max(
                1,
                env_int("HERMES_FEISHU_TEXT_BATCH_MAX_CHARS", _DEFAULT_TEXT_BATCH_MAX_CHARS),
            ),
            media_batch_delay_seconds=env_float(
                "HERMES_FEISHU_MEDIA_BATCH_DELAY_SECONDS", _DEFAULT_MEDIA_BATCH_DELAY_SECONDS
            ),
            webhook_host=str(
                extra.get("webhook_host") or os.getenv("FEISHU_WEBHOOK_HOST", _DEFAULT_WEBHOOK_HOST)
            ).strip(),
            webhook_port=int(
                extra.get("webhook_port") or os.getenv("FEISHU_WEBHOOK_PORT", str(_DEFAULT_WEBHOOK_PORT))
            ),
            webhook_path=(
                str(extra.get("webhook_path") or os.getenv("FEISHU_WEBHOOK_PATH", _DEFAULT_WEBHOOK_PATH)).strip()
                or _DEFAULT_WEBHOOK_PATH
            ),
            ws_reconnect_nonce=_coerce_required_int(extra.get("ws_reconnect_nonce"), default=30, min_value=0),
            ws_reconnect_interval=_coerce_required_int(extra.get("ws_reconnect_interval"), default=120, min_value=1),
            ws_ping_interval=_coerce_int(extra.get("ws_ping_interval"), default=None, min_value=1),
            ws_ping_timeout=_coerce_int(extra.get("ws_ping_timeout"), default=None, min_value=1),
            admins=admins,
            default_group_policy=default_group_policy,
            group_rules=group_rules,
            allow_bots=allow_bots,
            require_mention=_to_boolean(
                extra.get("require_mention", os.getenv("FEISHU_REQUIRE_MENTION", "true"))
            ),
            reply_thread=_env_boolean_default_true("FEISHU_REPLY_THREAD"),
            card_output=(
                _to_boolean(extra["card_output"])
                if "card_output" in extra
                else _env_boolean_default_false("FEISHU_CARD_OUTPUT")
            ),
        )

    def _apply_settings(self, settings: FeishuAdapterSettings) -> None:
        self._app_id = settings.app_id
        self._app_secret = settings.app_secret
        self._domain_name = settings.domain_name
        self._connection_mode = settings.connection_mode
        self._encrypt_key = settings.encrypt_key
        self._verification_token = settings.verification_token
        self._group_policy = settings.group_policy
        self._allowed_group_users = set(settings.allowed_group_users)
        self._admins = set(settings.admins)
        self._default_group_policy = settings.default_group_policy or settings.group_policy
        self._group_rules = settings.group_rules
        self._bot_open_id = settings.bot_open_id
        self._bot_user_id = settings.bot_user_id
        self._bot_name = settings.bot_name
        self._dedup_cache_size = settings.dedup_cache_size
        self._text_batch_delay_seconds = settings.text_batch_delay_seconds
        self._text_batch_split_delay_seconds = settings.text_batch_split_delay_seconds
        self._text_batch_max_messages = settings.text_batch_max_messages
        self._text_batch_max_chars = settings.text_batch_max_chars
        self._media_batch_delay_seconds = settings.media_batch_delay_seconds
        self._webhook_host = settings.webhook_host
        self._webhook_port = settings.webhook_port
        self._webhook_path = settings.webhook_path
        self._ws_reconnect_nonce = settings.ws_reconnect_nonce
        self._ws_reconnect_interval = settings.ws_reconnect_interval
        self._ws_ping_interval = settings.ws_ping_interval
        self._ws_ping_timeout = settings.ws_ping_timeout
        self._allow_bots = settings.allow_bots
        self._require_mention = settings.require_mention
        self._reply_thread_enabled_setting = settings.reply_thread
        self._card_output_enabled = settings.card_output

    def _build_event_handler(self) -> Any:
        if EventDispatcherHandler is None:
            return None
        return (
            EventDispatcherHandler.builder(
                self._encrypt_key,
                self._verification_token,
            )
            .register_p2_im_message_message_read_v1(self._on_message_read_event)
            .register_p2_im_message_receive_v1(self._on_message_event)
            .register_p2_im_message_reaction_created_v1(
                lambda data: self._on_reaction_event("im.message.reaction.created_v1", data)
            )
            .register_p2_im_message_reaction_deleted_v1(
                lambda data: self._on_reaction_event("im.message.reaction.deleted_v1", data)
            )
            .register_p2_card_action_trigger(self._on_card_action_trigger)
            .register_p2_im_chat_member_bot_added_v1(self._on_bot_added_to_chat)
            .register_p2_im_chat_member_bot_deleted_v1(self._on_bot_removed_from_chat)
            .register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(self._on_p2p_chat_entered)
            .register_p2_im_message_recalled_v1(self._on_message_recalled)
            .register_p2_customized_event(
                "drive.notice.comment_add_v1",
                self._on_drive_comment_event,
            )
            .register_p2_customized_event(
                "vc.bot.meeting_invited_v1",
                self._on_meeting_invited_event,
            )
            .build()
        )

    # =========================================================================
    # A2A delegate foreground runtime
    # =========================================================================

    def _delegate_route_key(
        self, *, chat_id: str, thread_id: Optional[str], user_id: Optional[str], chat_type: Optional[str]
    ) -> str:
        """Use the normal Hermes session identity for foreground route isolation."""
        try:
            from gateway.session import build_session_key

            return build_session_key(
                self.build_source(
                    chat_id=chat_id,
                    chat_type=chat_type or "dm",
                    user_id=user_id,
                    thread_id=thread_id,
                ),
                # A foreground loop owns a blocking input queue.  Unlike the
                # regular group-thread transcript (which may be intentionally
                # shared), it must never let another participant feed that
                # queue.  Keep both group and threaded routes per-user.
                group_sessions_per_user=True,
                thread_sessions_per_user=True,
            )
        except Exception:
            return json.dumps(
                {"platform": "feishu", "chat_id": chat_id, "thread_id": thread_id,
                 "user_id": user_id, "chat_type": chat_type},
                sort_keys=True,
            )

    def _register_delegate_route(self, route: _FeishuDelegateRoute) -> None:
        with self._delegate_routes_lock:
            self._delegate_routes[route.key] = route

    def _unregister_delegate_route(self, route: _FeishuDelegateRoute) -> None:
        with self._delegate_routes_lock:
            if self._delegate_routes.get(route.key) is route:
                self._delegate_routes.pop(route.key, None)
        self._clear_delegate_stream_state(route.key)

    def _get_delegate_route(
        self, *, chat_id: str, thread_id: Optional[str], user_id: Optional[str], chat_type: Optional[str]
    ) -> Optional[_FeishuDelegateRoute]:
        key = self._delegate_route_key(
            chat_id=chat_id, thread_id=thread_id, user_id=user_id, chat_type=chat_type
        )
        with self._delegate_routes_lock:
            return self._delegate_routes.get(key)

    def _schedule_delegate_output(self, coro) -> None:
        loop = self._delegate_loop or self._loop
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is loop:
            running_loop.create_task(coro)
        elif loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, loop)
        else:
            logger.warning("[Feishu] Dropping delegate output because adapter loop is unavailable")
            coro.close()

    def _get_delegate_stream_state(self, route_key: str) -> _FeishuDelegateStreamState:
        with self._delegate_routes_lock:
            state = self._delegate_stream_states.get(route_key)
            if state is None:
                state = _FeishuDelegateStreamState()
                self._delegate_stream_states[route_key] = state
        if state.lock is None:
            state.lock = asyncio.Lock()
        return state

    @staticmethod
    def _reset_delegate_stream_segment_locked(state: _FeishuDelegateStreamState) -> None:
        if state.flush_task is not None and not state.flush_task.done():
            state.flush_task.cancel()
        state.accumulated_text = ""
        state.pending_text = ""
        state.message_id = None
        state.last_rendered_text = ""
        state.last_flush_ts = 0.0
        state.flush_task = None

    def _clear_delegate_stream_state(self, route_key: str) -> None:
        with self._delegate_routes_lock:
            state = self._delegate_stream_states.pop(route_key, None)
        if state is not None and state.flush_task is not None and not state.flush_task.done():
            state.flush_task.cancel()

    def _close_delegate_routes(self) -> None:
        """Wake blocked foreground readers and discard all adapter-local state."""
        with self._delegate_routes_lock:
            routes = list(self._delegate_routes.values())
            self._delegate_routes.clear()
            states = list(self._delegate_stream_states.values())
            self._delegate_stream_states.clear()
        for route in routes:
            close = getattr(route.input_adapter, "close", None)
            if callable(close):
                close()
        for state in states:
            if state.flush_task is not None and not state.flush_task.done():
                state.flush_task.cancel()

    def _delegate_stream_edit_interval(self) -> float:
        try:
            return max(0.0, float(getattr(
                self, "_delegate_stream_edit_interval", DEFAULT_DELEGATE_STREAM_EDIT_INTERVAL
            )))
        except (TypeError, ValueError):
            return DEFAULT_DELEGATE_STREAM_EDIT_INTERVAL

    def _split_delegate_stream_content(self, text: str) -> List[str]:
        return self.truncate_message(str(text or ""), max(1, self.MAX_MESSAGE_LENGTH - 64)) if text else []

    async def _flush_delegate_stream_after_delay(
        self, *, route_key: str, chat_id: str, metadata: Optional[Dict[str, Any]], delay: float
    ) -> None:
        try:
            await asyncio.sleep(max(0.0, delay))
            state = self._delegate_stream_states.get(route_key)
            if state is None or state.lock is None:
                return
            async with state.lock:
                if state.flush_task is not asyncio.current_task():
                    return
                state.flush_task = None
                await self._flush_delegate_stream_locked(
                    state=state, chat_id=chat_id, metadata=metadata, route_key=route_key, force=True
                )
        except asyncio.CancelledError:
            return

    async def _flush_delegate_stream_locked(
        self,
        *,
        state: _FeishuDelegateStreamState,
        chat_id: str,
        metadata: Optional[Dict[str, Any]],
        route_key: Optional[str] = None,
        force: bool = False,
    ) -> None:
        if not state.pending_text and state.message_id:
            return
        if state.message_id and state.pending_text and not force:
            interval = self._delegate_stream_edit_interval()
            elapsed = time.monotonic() - state.last_flush_ts
            if elapsed < interval:
                if route_key and (state.flush_task is None or state.flush_task.done()):
                    state.flush_task = asyncio.create_task(self._flush_delegate_stream_after_delay(
                        route_key=route_key, chat_id=chat_id, metadata=metadata, delay=interval - elapsed,
                    ))
                return
        if state.flush_task is not None and not state.flush_task.done():
            state.flush_task.cancel()
            state.flush_task = None
        groups = self._split_delegate_stream_content(state.accumulated_text)
        if not groups:
            return
        if not state.message_id:
            for group in groups:
                result = await self.send(chat_id, group, metadata=metadata)
                if not result.success:
                    return
                if result.message_id:
                    state.message_id = str(result.message_id)
        elif len(groups) == 1:
            current = groups[0]
            if current != state.last_rendered_text:
                result = await self.edit_message(chat_id, state.message_id, current)
                if not result.success:
                    result = await self.send(chat_id, state.pending_text, metadata=metadata)
                    if not result.success:
                        return
                    if result.message_id:
                        state.message_id = str(result.message_id)
        else:
            result = await self.edit_message(chat_id, state.message_id, groups[0])
            if not result.success:
                result = await self.send(chat_id, groups[0], metadata=metadata)
                if not result.success:
                    return
                if result.message_id:
                    state.message_id = str(result.message_id)
            for group in groups[1:]:
                result = await self.send(chat_id, group, metadata=metadata)
                if not result.success:
                    return
                if result.message_id:
                    state.message_id = str(result.message_id)
        state.accumulated_text = groups[-1]
        state.last_rendered_text = groups[-1]
        state.pending_text = ""
        state.last_flush_ts = time.monotonic()

    async def handle_delegate_ai_delta(
        self, *, route_key: str, chat_id: str, content: str, metadata: Optional[Dict[str, Any]]
    ) -> None:
        state = self._get_delegate_stream_state(route_key)
        assert state.lock is not None
        async with state.lock:
            state.accumulated_text += str(content or "")
            state.pending_text += str(content or "")
            await self._flush_delegate_stream_locked(
                state=state, chat_id=chat_id, metadata=metadata, route_key=route_key
            )

    async def handle_delegate_stream_segment_break(
        self, *, route_key: str, chat_id: str, metadata: Optional[Dict[str, Any]]
    ) -> None:
        state = self._delegate_stream_states.get(route_key)
        if state is None or state.lock is None:
            return
        async with state.lock:
            await self._flush_delegate_stream_locked(
                state=state, chat_id=chat_id, metadata=metadata, route_key=route_key, force=True
            )
            self._reset_delegate_stream_segment_locked(state)

    def build_delegate_foreground_runtime(
        self,
        *,
        channel_id: str,
        thread_ts: Optional[str],
        user_id: Optional[str] = None,
        chat_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            self._delegate_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        route_key = self._delegate_route_key(
            chat_id=channel_id, thread_id=thread_ts, user_id=user_id, chat_type=chat_type
        )

        def _input_factory():
            route = _FeishuDelegateRoute(
                key=route_key, chat_id=channel_id, thread_id=thread_ts, user_id=user_id,
                chat_type=chat_type, input_adapter=None,
            )
            input_adapter = _FeishuDelegateInputAdapter(self, route)
            route.input_adapter = input_adapter
            return input_adapter

        return {
            "output": _FeishuDelegateOutputAdapter(
                self, chat_id=channel_id, thread_id=thread_ts, user_id=user_id, chat_type=chat_type
            ),
            "input_factory": _input_factory,
            "metadata": {"thread_id": thread_ts} if thread_ts else None,
        }

    async def _maybe_route_delegate_foreground_message(
        self,
        *,
        text: str,
        chat_id: str,
        thread_id: Optional[str],
        user_id: Optional[str],
        chat_type: Optional[str],
    ) -> bool:
        # Some lightweight embedders construct an adapter shell only for
        # normal message parsing and bypass ``__init__``.  Without foreground
        # runtime state there cannot be a route to claim, so preserve the
        # historical pass-through behaviour.
        if not hasattr(self, "_delegate_routes_lock"):
            return False
        route = self._get_delegate_route(
            chat_id=chat_id, thread_id=thread_id, user_id=user_id, chat_type=chat_type
        )
        if route is None or not callable(getattr(route.input_adapter, "push_line", None)):
            return False
        await self.handle_delegate_stream_segment_break(
            route_key=route.key, chat_id=chat_id,
            metadata={"thread_id": thread_id} if thread_id else None,
        )
        if route.input_adapter.push_line(text):
            return True
        self._unregister_delegate_route(route)
        return False

    async def _maybe_route_delegate_interaction_message(
        self,
        *,
        text: str,
        chat_id: str,
        thread_id: Optional[str],
        user_id: Optional[str],
        chat_type: Optional[str],
    ) -> bool:
        del chat_type
        candidate = None
        for state in self._delegate_interactions.values():
            if state.get("kind") != "clarify" or not state.get("awaiting_text") or state.get("resolved"):
                continue
            if str(state.get("chat_id") or "") != str(chat_id or ""):
                continue
            if (state.get("thread_id") or None) != (thread_id or None):
                continue
            owner = str(state.get("user_id") or "")
            if owner and owner != str(user_id or ""):
                continue
            candidate = state
            break
        if candidate is None:
            return False
        responder = candidate.get("responder")
        interaction_id = str(candidate.get("interaction_id") or "")
        if not interaction_id or not callable(responder) or not responder(interaction_id, "clarify", str(text or "").strip()):
            return False
        candidate["resolved"] = True
        candidate["awaiting_text"] = False
        await self._send_delegate_resolution(candidate, "✅ Clarification response sent.")
        return True

    def _get_sdk_executor(self) -> concurrent.futures.ThreadPoolExecutor:
        """Return the adapter-owned executor for blocking Feishu SDK calls.

        Recreates the pool if it was never built or was shut down by an
        *external* teardown of the loop's default executor, so that can no
        longer permanently wedge sends (#10849). Refuses to resurrect once
        the adapter itself is closing — a real disconnect/shutdown stays shut.
        """
        lock = getattr(self, "_sdk_executor_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._sdk_executor_lock = lock
        with lock:
            if getattr(self, "_sdk_executor_closing", False):
                raise RuntimeError("Feishu adapter is shutting down; SDK executor unavailable")
            executor = getattr(self, "_sdk_executor", None)
            if executor is None or getattr(executor, "_shutdown", False):
                executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=10,
                    thread_name_prefix="hermes-feishu-sdk",
                )
                self._sdk_executor = executor
            return executor

    async def _run_blocking(self, func, *args):
        """Run a blocking Feishu SDK call on the adapter-owned thread pool."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._get_sdk_executor(), func, *args)

    def _shutdown_sdk_executor(self) -> None:
        """Stop the adapter-owned SDK executor without touching the loop default."""
        lock = getattr(self, "_sdk_executor_lock", None)
        if lock is None:
            return
        with lock:
            self._sdk_executor_closing = True
            executor = getattr(self, "_sdk_executor", None)
            self._sdk_executor = None
        if executor is None:
            return
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            executor.shutdown(wait=False)

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        """Connect to Feishu/Lark."""
        # A fresh connect (or reconnect) re-arms the SDK executor after a prior
        # disconnect set the closing flag.
        self._sdk_executor_closing = False
        if not self._app_id or not self._app_secret:
            logger.error("[Feishu] FEISHU_APP_ID or FEISHU_APP_SECRET not set")
            return False
        if self._connection_mode not in {"websocket", "webhook"}:
            logger.error(
                "[Feishu] Unsupported FEISHU_CONNECTION_MODE=%s. Supported modes: websocket, webhook.",
                self._connection_mode,
            )
            return False
        if self._connection_mode == "webhook" and not (self._verification_token or self._encrypt_key):
            logger.error(
                "[Feishu] Webhook mode requires FEISHU_VERIFICATION_TOKEN or FEISHU_ENCRYPT_KEY."
            )
            return False
        if not await asyncio.to_thread(_load_lark_oapi):
            logger.error("[Feishu] lark-oapi not installed")
            return False

        try:
            self._app_lock_identity = self._app_id
            acquired, existing = acquire_scoped_lock(
                _FEISHU_APP_LOCK_SCOPE,
                self._app_lock_identity,
                metadata={"platform": self.platform.value},
            )
            if not acquired:
                owner_pid = existing.get("pid") if isinstance(existing, dict) else None
                message = (
                    "Another local Hermes gateway is already using this Feishu app_id"
                    + (f" (PID {owner_pid})." if owner_pid else ".")
                    + " Stop the other gateway before starting a second Feishu websocket client."
                )
                logger.error("[Feishu] %s", message)
                self._set_fatal_error("feishu_app_lock", message, retryable=False)
                return False

            self._loop = asyncio.get_running_loop()
            await self._connect_with_retry()
            self._mark_connected()
            logger.info("[Feishu] Connected in %s mode (%s)", self._connection_mode, self._domain_name)
            return True
        except Exception as exc:
            await self._release_app_lock()
            message = f"Feishu startup failed: {exc}"
            self._set_fatal_error("feishu_connect_error", message, retryable=True)
            logger.error("[Feishu] Failed to connect: %s", exc, exc_info=True)
            return False

    async def disconnect(self) -> None:
        """Disconnect from Feishu/Lark."""
        self._running = False
        self._close_delegate_routes()
        await self._close_card_sessions()
        await self._cancel_pending_tasks(self._pending_text_batch_tasks)
        await self._cancel_pending_tasks(self._pending_media_batch_tasks)
        self._reset_batch_buffers()

        # Send a WebSocket CLOSE frame to Feishu BEFORE tearing down the
        # thread loop. Without this, Feishu's server never learns the
        # connection is dead and continues routing messages to the stale
        # endpoint — the channel goes silent until the server-side
        # CLOSE-WAIT expires (minutes to hours). See issue #10202.
        #
        # ``_disable_websocket_auto_reconnect()`` nils ``self._ws_client``,
        # so capture the client reference first.
        ws_client = self._ws_client
        ws_thread_loop = self._ws_thread_loop
        self._disable_websocket_auto_reconnect()
        await self._stop_webhook_server()

        if (
            ws_client is not None
            and ws_thread_loop is not None
            and not ws_thread_loop.is_closed()
            and hasattr(ws_client, "_disconnect")
        ):
            try:
                future = asyncio.run_coroutine_threadsafe(
                    ws_client._disconnect(), ws_thread_loop
                )
                # 5s is generous — the CLOSE frame is a single WebSocket
                # control frame. If it takes longer than that the
                # connection is already wedged and we gain nothing by
                # waiting further.
                await asyncio.wait_for(asyncio.wrap_future(future), timeout=5.0)
                logger.debug("[Feishu] Sent WebSocket CLOSE frame to Feishu")
            except asyncio.TimeoutError:
                logger.warning(
                    "[Feishu] CLOSE frame not acknowledged within 5s — "
                    "Feishu may briefly route messages to the stale "
                    "connection until server-side timeout"
                )
            except Exception as exc:
                logger.debug(
                    "[Feishu] Could not send WebSocket CLOSE frame: %s",
                    exc,
                    exc_info=True,
                )

        if ws_thread_loop is not None and not ws_thread_loop.is_closed():
            logger.debug("[Feishu] Cancelling websocket thread tasks and stopping loop")

            def cancel_all_tasks() -> None:
                tasks = [t for t in asyncio.all_tasks(ws_thread_loop) if not t.done()]
                logger.debug("[Feishu] Found %d pending tasks in websocket thread", len(tasks))
                for task in tasks:
                    task.cancel()
                ws_thread_loop.call_later(0.1, ws_thread_loop.stop)

            ws_thread_loop.call_soon_threadsafe(cancel_all_tasks)

        ws_future = self._ws_future
        if ws_future is not None:
            try:
                logger.debug("[Feishu] Waiting for websocket thread to exit (timeout=10s)")
                await asyncio.wait_for(asyncio.shield(ws_future), timeout=10.0)
                logger.debug("[Feishu] Websocket thread exited cleanly")
            except asyncio.TimeoutError:
                logger.warning("[Feishu] Websocket thread did not exit within 10s - may be stuck")
            except asyncio.CancelledError:
                logger.debug("[Feishu] Websocket thread cancelled during disconnect")
            except Exception as exc:
                logger.debug("[Feishu] Websocket thread exited with error: %s", exc, exc_info=True)

        self._ws_future = None
        self._ws_thread_loop = None
        self._loop = None
        self._event_handler = None
        self._shutdown_sdk_executor()
        self._persist_seen_message_ids()
        await self._release_app_lock()

        self._mark_disconnected()
        logger.info("[Feishu] Disconnected")

    async def _close_card_sessions(self) -> None:
        """Seal every live card so a shutdown never leaves one 'generating'."""
        manager = getattr(self, "_card_manager", None)
        if manager is None:
            return
        try:
            await manager.close_all()
        except Exception:
            logger.debug("[Feishu] card teardown failed", exc_info=True)
        finally:
            getattr(self, "_delegate_card_blocks", {}).clear()
            getattr(self, "_delegate_card_locks", {}).clear()
            getattr(self, "_delegate_card_streamed", set()).clear()
            getattr(self, "_card_media_dispatched", {}).clear()

    async def _cancel_pending_tasks(self, tasks: Dict[str, asyncio.Task]) -> None:
        pending = [task for task in tasks.values() if task and not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        tasks.clear()

    def _reset_batch_buffers(self) -> None:
        self._pending_text_batches.clear()
        self._pending_text_batch_counts.clear()
        self._pending_media_batches.clear()

    def _disable_websocket_auto_reconnect(self) -> None:
        if self._ws_client is None:
            return
        try:
            setattr(self._ws_client, "_auto_reconnect", False)
        except Exception:
            pass
        finally:
            self._ws_client = None

    async def _stop_webhook_server(self) -> None:
        if self._webhook_runner is None:
            return
        try:
            await self._webhook_runner.cleanup()
        finally:
            self._webhook_runner = None
            self._webhook_site = None

    # =========================================================================
    # Outbound — send / edit / send_image / send_voice / …
    # =========================================================================

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send a Feishu message."""
        if not self._client:
            return SendResult(success=False, error="Not connected")

        # A file is never card content.  Anything the gateway's own extraction
        # did not already take out (mid-turn commentary, delegate output) is
        # pulled out here so it arrives as its own message instead of rendering
        # as a literal path inside the card.  No-op outside a card turn.
        content, _owed_attachments = self._split_card_mode_attachments(
            chat_id=chat_id, content=content, metadata=metadata,
        )

        # Card path first: inside an agent turn the reply and its execution
        # chrome belong to one card, not to a fan of separate bubbles.
        # ``None`` means "not card-eligible" (no turn, cards disabled, or a
        # CardKit failure put this route back on the legacy path).
        card_result = await self._send_via_card(
            chat_id=chat_id, content=content, reply_to=reply_to, metadata=metadata,
        )
        if card_result is not None:
            if _owed_attachments:
                # Text first, then the files: the card names them, the bubbles
                # below it carry them.
                await self._dispatch_card_attachments(
                    chat_id=chat_id,
                    attachments=_owed_attachments,
                    metadata=metadata,
                )
            return card_result

        formatted = self.format_message(content)
        chunks = self.truncate_message(formatted, self.MAX_MESSAGE_LENGTH)
        # When chunking splits a long markdown response, an individual chunk
        # can end up as plain prose that doesn't match the per-chunk hint
        # regex — so it would be sent as ``msg_type=text`` and the user would
        # see literal ``**bold``/``## heading``/code fences in the Feishu
        # client while other chunks render correctly. Lock the markdown
        # decision at the whole-message level so every chunk consistently
        # uses ``post``. See #26841.
        prefer_post = bool(_MARKDOWN_HINT_RE.search(formatted))
        last_response = None

        try:
            for chunk in chunks:
                msg_type, payload = self._build_outbound_payload(
                    chunk, prefer_post=prefer_post,
                )
                try:
                    response = await self._feishu_send_with_retry(
                        chat_id=chat_id,
                        msg_type=msg_type,
                        payload=payload,
                        reply_to=reply_to,
                        metadata=metadata,
                    )
                except Exception as exc:
                    if msg_type != "post" or not _POST_CONTENT_INVALID_RE.search(str(exc)):
                        raise
                    logger.warning("[Feishu] Invalid post payload rejected by API; falling back to plain text")
                    response = await self._feishu_send_with_retry(
                        chat_id=chat_id,
                        msg_type="text",
                        payload=json.dumps({"text": _strip_markdown_to_plain_text(chunk)}, ensure_ascii=False),
                        reply_to=reply_to,
                        metadata=metadata,
                    )
                if (
                    msg_type == "post"
                    and not self._response_succeeded(response)
                    and _POST_CONTENT_INVALID_RE.search(str(getattr(response, "msg", "") or ""))
                ):
                    logger.warning("[Feishu] Post payload rejected by API response; falling back to plain text")
                    response = await self._feishu_send_with_retry(
                        chat_id=chat_id,
                        msg_type="text",
                        payload=json.dumps({"text": _strip_markdown_to_plain_text(chunk)}, ensure_ascii=False),
                        reply_to=reply_to,
                        metadata=metadata,
                    )
                last_response = response

            if _owed_attachments:
                # The card declined this send (CardKit cooldown after a
                # failure) but the files were already taken out of the text,
                # so they still have to reach the user.
                await self._dispatch_card_attachments(
                    chat_id=chat_id,
                    attachments=_owed_attachments,
                    metadata=metadata,
                )
            return self._finalize_send_result(last_response, "send failed")
        except Exception as exc:
            logger.error("[Feishu] Send error: %s", exc, exc_info=True)
            return SendResult(success=False, error=str(exc))

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
        *,
        finalize: bool = False,
    ) -> SendResult:
        """Edit a previously sent Feishu text/post message."""
        if not self._client:
            return SendResult(success=False, error="Not connected")

        # Streamed edits of a card block rewrite that block in place; the
        # ``im`` edit API cannot touch a card at all, so this has to be
        # handled before the text/post path.
        card_result = await self._edit_via_card(message_id, content)
        if card_result is not None:
            return card_result

        content = self.format_message(content)
        try:
            msg_type, payload = self._build_outbound_payload(content)
            body = self._build_update_message_body(msg_type=msg_type, content=payload)
            request = self._build_update_message_request(message_id=message_id, request_body=body)
            response = await self._run_blocking(self._client.im.v1.message.update, request)
            result = self._finalize_send_result(response, "update failed")
            if not result.success and msg_type == "post" and _POST_CONTENT_INVALID_RE.search(result.error or ""):
                logger.warning("[Feishu] Invalid post update payload rejected by API; falling back to plain text")
                fallback_body = self._build_update_message_body(
                    msg_type="text",
                    content=json.dumps({"text": _strip_markdown_to_plain_text(content)}, ensure_ascii=False),
                )
                fallback_request = self._build_update_message_request(message_id=message_id, request_body=fallback_body)
                fallback_response = await self._run_blocking(self._client.im.v1.message.update, fallback_request)
                result = self._finalize_send_result(fallback_response, "update failed")
            if result.success:
                result.message_id = message_id
            return result
        except Exception as exc:
            logger.error("[Feishu] Failed to edit message %s: %s", message_id, exc, exc_info=True)
            return SendResult(success=False, error=str(exc))

    # Template attrs for the shared _format_exec_approval core. The card
    # header carries the title, so the text core starts at the code fence.
    _EA_HEADER = ""
    _EA_REASON_LABEL = "**Reason:** "
    _EA_SMART_DENY_LINE = "\n\n**Smart DENY:** owner override applies to this one operation only."
    _EA_CMD_BUDGET = 3000

    async def _send_delegate_resolution(self, state: Dict[str, Any], text: str) -> None:
        # "✅ Delegate interaction resolved." / "✅ Clarification response
        # sent." acknowledge a button press on an interaction card.  They are
        # not the delegate's answer, so they must not be appended to the card
        # that answer is streaming into.
        metadata: Dict[str, Any] = {_CARD_BYPASS_METADATA_KEY: True}
        if state.get("thread_id"):
            metadata["thread_id"] = state.get("thread_id")
        try:
            await self.send(
                str(state.get("chat_id") or ""),
                text,
                metadata=metadata,
            )
        except Exception:
            logger.debug("[Feishu] delegate resolution notice failed", exc_info=True)

    async def send_delegate_exec_approval(
        self,
        *,
        chat_id: str,
        payload: Dict[str, Any],
        responder,
        metadata: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
    ) -> SendResult:
        interaction_id = str(payload.get("interaction_id") or "")
        if not interaction_id or not self._client:
            return SendResult(success=False, error="Missing A2A interaction id or client")
        try:
            prefix = {"hermes_delegate_kind": "approval", "interaction_id": interaction_id}
            def _btn(label: str, choice: str, button_type: str = "default") -> dict:
                return {"tag": "button", "text": {"tag": "plain_text", "content": label}, "type": button_type, "value": {**prefix, "choice": choice}}
            actions = [_btn("✅ Allow Once", "once", "primary")]
            if payload.get("allow_session", True):
                actions.append(_btn("✅ Session", "session"))
            if payload.get("allow_permanent", True):
                actions.append(_btn("✅ Always", "always"))
            actions.append(_btn("❌ Deny", "deny", "danger"))
            card = {
                "config": {"wide_screen_mode": True},
                "header": {"title": {"content": "⚠️ Delegate Approval Required", "tag": "plain_text"}, "template": "orange"},
                "elements": [
                    {"tag": "markdown", "content": self._format_exec_approval(str(payload.get("command") or ""), str(payload.get("description") or "dangerous command"), False)},
                    {"tag": "action", "actions": actions},
                ],
            }
            response = await self._feishu_send_with_retry(
                chat_id=chat_id,
                msg_type="interactive",
                payload=json.dumps(card, ensure_ascii=False),
                reply_to=(metadata or {}).get("thread_id"),
                metadata=metadata,
            )
            result = self._finalize_send_result(response, "send_delegate_exec_approval failed")
            if result.success:
                self._delegate_interactions[interaction_id] = {
                    "kind": "approval", "interaction_id": interaction_id, "responder": responder,
                    "chat_id": chat_id, "thread_id": (metadata or {}).get("thread_id"), "user_id": user_id,
                    "message_id": result.message_id or "", "command": str(payload.get("command") or ""), "resolved": False,
                }
                self._trim_oldest_dict_entries(self._delegate_interactions, self._DELEGATE_INTERACTIONS_MAX)
            return result
        except Exception as exc:
            logger.warning("[Feishu] send_delegate_exec_approval failed: %s", exc, exc_info=True)
            return SendResult(success=False, error=str(exc))

    async def send_delegate_clarify(
        self,
        *,
        chat_id: str,
        payload: Dict[str, Any],
        responder,
        metadata: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
    ) -> SendResult:
        interaction_id = str(payload.get("interaction_id") or "")
        if not interaction_id or not self._client:
            return SendResult(success=False, error="Missing A2A interaction id or client")
        choices = [str(item) for item in (payload.get("choices") or []) if str(item).strip()]
        state = {
            "kind": "clarify", "interaction_id": interaction_id, "responder": responder,
            "chat_id": chat_id, "thread_id": (metadata or {}).get("thread_id"), "user_id": user_id,
            "question": str(payload.get("question") or "Clarification required"), "choices": choices,
            "multi_select": bool(payload.get("multi_select")), "selected": [],
            "awaiting_text": not bool(choices), "resolved": False,
        }
        try:
            if not choices:
                card = {
                    "config": {"wide_screen_mode": True},
                    "header": {"title": {"content": "❓ Delegate Clarification", "tag": "plain_text"}, "template": "blue"},
                    "elements": [{"tag": "markdown", "content": state["question"][:3000]}],
                }
            else:
                base = {"hermes_delegate_kind": "clarify", "interaction_id": interaction_id}
                actions = [
                    {"tag": "button", "text": {"tag": "plain_text", "content": choice[:200]}, "type": "primary" if index == 0 else "default", "value": {**base, "token": str(index)}}
                    for index, choice in enumerate(choices)
                ]
                actions.append({"tag": "button", "text": {"tag": "plain_text", "content": "Other (type answer)"}, "type": "default", "value": {**base, "token": "other"}})
                if state["multi_select"]:
                    actions.append({"tag": "button", "text": {"tag": "plain_text", "content": "Submit selection"}, "type": "primary", "value": {**base, "token": "submit"}})
                card = {
                    "config": {"wide_screen_mode": True},
                    "header": {"title": {"content": "❓ Delegate Clarification", "tag": "plain_text"}, "template": "blue"},
                    "elements": [{"tag": "markdown", "content": state["question"][:3000]}, {"tag": "action", "actions": actions}],
                }
            response = await self._feishu_send_with_retry(
                chat_id=chat_id, msg_type="interactive", payload=json.dumps(card, ensure_ascii=False),
                reply_to=state["thread_id"], metadata=metadata,
            )
            result = self._finalize_send_result(response, "send_delegate_clarify failed")
            if result.success:
                state["message_id"] = result.message_id or ""
                self._delegate_interactions[interaction_id] = state
                self._trim_oldest_dict_entries(self._delegate_interactions, self._DELEGATE_INTERACTIONS_MAX)
            return result
        except Exception as exc:
            logger.warning("[Feishu] send_delegate_clarify failed: %s", exc, exc_info=True)
            return SendResult(success=False, error=str(exc))

    async def resolve_delegate_interaction(self, payload: Dict[str, Any]) -> None:
        interaction_id = str(payload.get("interaction_id") or "")
        state = self._delegate_interactions.get(interaction_id)
        if state is None:
            return
        state["resolved"] = True
        state["awaiting_text"] = False
        await self._send_delegate_resolution(state, "✅ Delegate interaction resolved.")

    async def send_clarify(
        self,
        chat_id: str,
        question: str,
        choices: Optional[list],
        clarify_id: str,
        session_key: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Render multi-choice clarify prompts as Feishu interactive cards."""
        if not choices:
            return await super().send_clarify(
                chat_id=chat_id,
                question=question,
                choices=choices,
                clarify_id=clarify_id,
                session_key=session_key,
                metadata=metadata,
            )
        if not self._client:
            return SendResult(success=False, error="Not connected")

        normalized_choices = [str(choice) for choice in choices]
        question_text = str(question or "Please choose an option.")

        def _button(label: str, index: int | str, button_type: str = "default") -> Dict[str, Any]:
            return {
                "tag": "button",
                "text": {"tag": "plain_text", "content": label[:200]},
                "type": button_type,
                "value": {
                    "hermes_clarify_action": "select" if index != "other" else "other",
                    "clarify_id": str(clarify_id),
                    "index": index,
                },
            }

        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"content": "❓ Clarification needed", "tag": "plain_text"},
                "template": "blue",
            },
            "elements": [
                {"tag": "markdown", "content": question_text[:3000]},
                {
                    "tag": "action",
                    "actions": [
                        _button(choice.strip() or f"Option {index + 1}", index, "primary" if index == 0 else "default")
                        for index, choice in enumerate(normalized_choices)
                    ] + [_button("Other (type answer)", "other")],
                },
            ],
        }
        try:
            response = await self._feishu_send_with_retry(
                chat_id=chat_id,
                msg_type="interactive",
                payload=json.dumps(card, ensure_ascii=False),
                reply_to=None,
                metadata=metadata,
            )
            result = self._finalize_send_result(response, "send_clarify failed")
            if result.success:
                self._clarify_choices[str(clarify_id)] = {
                    "choices": normalized_choices,
                    "chat_id": chat_id,
                    "session_key": session_key,
                }
            return result
        except Exception as exc:
            logger.warning("[Feishu] send_clarify failed: %s", exc, exc_info=True)
            return SendResult(success=False, error=str(exc))

    @staticmethod
    def _build_resolved_clarify_card(*, choice: Optional[str], user_name: str) -> Dict[str, Any]:
        waiting_for_text = choice is None
        title = "✏️ Waiting for typed answer" if waiting_for_text else "✅ Clarification selected"
        content = (
            f"Waiting for a typed answer from **{user_name}**"
            if waiting_for_text
            else f"Selected **{choice}** by **{user_name}**"
        )
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"content": title, "tag": "plain_text"},
                "template": "blue" if waiting_for_text else "green",
            },
            "elements": [{"tag": "markdown", "content": content}],
        }

    async def send_exec_approval(
        self, chat_id: str, command: str, session_key: str,
        description: str = "dangerous command",
        metadata: Optional[Dict[str, Any]] = None,
        allow_permanent: bool = True,
        allow_session: bool = True,
        smart_denied: bool = False,
    ) -> SendResult:
        """Send an interactive card with approval buttons.

        The buttons carry ``hermes_action`` in their value dict so that
        ``_handle_card_action_event`` can intercept them and call
        ``resolve_gateway_approval()`` to unblock the waiting agent thread.
        """
        if not self._client:
            return SendResult(success=False, error="Not connected")

        try:
            approval_id = next(self._approval_counter)

            def _btn(label: str, action_name: str, btn_type: str = "default") -> dict:
                return {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": label},
                    "type": btn_type,
                    "value": {"hermes_action": action_name, "approval_id": approval_id},
                }

            actions = [_btn("✅ Allow Once", "approve_once", "primary")]
            if not smart_denied and allow_session:
                actions.append(_btn("✅ Session", "approve_session"))
                if allow_permanent:
                    actions.append(_btn("✅ Always", "approve_always"))
            actions.append(_btn("❌ Deny", "deny", "danger"))
            card = {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"content": "⚠️ Command Approval Required", "tag": "plain_text"},
                    "template": "orange",
                },
                "elements": [
                    {
                        "tag": "markdown",
                        "content": self._format_exec_approval(command, description, smart_denied),
                    },
                    {
                        "tag": "action",
                        "actions": actions,
                    },
                ],
            }

            payload = json.dumps(card, ensure_ascii=False)
            response = await self._feishu_send_with_retry(
                chat_id=chat_id,
                msg_type="interactive",
                payload=payload,
                reply_to=None,
                metadata=metadata,
            )

            result = self._finalize_send_result(response, "send_exec_approval failed")
            if result.success:
                self._approval_state[approval_id] = {
                    "session_key": session_key,
                    "message_id": result.message_id or "",
                    "chat_id": chat_id,
                }
            return result
        except Exception as exc:
            logger.warning("[Feishu] send_exec_approval failed: %s", exc)
            return SendResult(success=False, error=str(exc))

    @staticmethod
    def _build_update_prompt_card(*, prompt: str, default: str, prompt_id: int) -> Dict[str, Any]:
        default_hint = f"\n\nDefault: `{default}`" if default else ""

        def _btn(label: str, answer: str, btn_type: str) -> dict:
            return {
                "tag": "button",
                "text": {"tag": "plain_text", "content": label},
                "type": btn_type,
                "value": {
                    "hermes_update_prompt_action": answer,
                    "update_prompt_id": prompt_id,
                },
            }

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"content": "⚕ Update Needs Your Input", "tag": "plain_text"},
                "template": "orange",
            },
            "elements": [
                {"tag": "markdown", "content": f"{prompt}{default_hint}"},
                {
                    "tag": "action",
                    "actions": [
                        _btn("✓ Yes", "y", "primary"),
                        _btn("✗ No", "n", "danger"),
                    ],
                },
            ],
        }

    async def send_update_prompt(
        self, chat_id: str, prompt: str, default: str = "",
        session_key: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send an interactive update prompt with Yes/No buttons."""
        if not self._client:
            return SendResult(success=False, error="Not connected")

        try:
            prompt_id = next(self._update_prompt_counter)
            payload = json.dumps(
                self._build_update_prompt_card(prompt=prompt, default=default, prompt_id=prompt_id),
                ensure_ascii=False,
            )
            response = await self._feishu_send_with_retry(
                chat_id=chat_id,
                msg_type="interactive",
                payload=payload,
                reply_to=None,
                metadata=metadata,
            )

            result = self._finalize_send_result(response, "send_update_prompt failed")
            if result.success:
                self._update_prompt_state[prompt_id] = {
                    "session_key": session_key,
                    "message_id": result.message_id or "",
                    "chat_id": chat_id,
                }
            return result
        except Exception as exc:
            logger.warning("[Feishu] send_update_prompt failed: %s", exc)
            return SendResult(success=False, error=str(exc))

    @staticmethod
    def _build_resolved_approval_card(*, choice: str, user_name: str) -> Dict[str, Any]:
        """Build raw card JSON for a resolved approval action."""
        icon = "❌" if choice == "deny" else "✅"
        label = _APPROVAL_LABEL_MAP.get(choice, "Resolved")
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"content": f"{icon} {label}", "tag": "plain_text"},
                "template": "red" if choice == "deny" else "green",
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": f"{icon} **{label}** by {user_name}",
                },
            ],
        }

    @staticmethod
    def _build_resolved_update_prompt_card(*, answer: str, user_name: str) -> Dict[str, Any]:
        yes = answer == "y"
        label = "Yes" if yes else "No"
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"content": f"{'✅' if yes else '❌'} Update prompt answered: {label}", "tag": "plain_text"},
                "template": "green" if yes else "red",
            },
            "elements": [
                {"tag": "markdown", "content": f"Answered by **{user_name}**"},
            ],
        }

    @staticmethod
    def _write_update_prompt_response(answer: str) -> None:
        response_path = get_hermes_home() / ".update_response"
        tmp_path = response_path.with_suffix(".tmp")
        tmp_path.write_text(answer, encoding="utf-8")
        tmp_path.replace(response_path)

    # =========================================================================
    # Card output — three-element CardKit cards
    # =========================================================================

    def _card_output_active(self) -> bool:
        manager = getattr(self, "_card_manager", None)
        return manager is not None and manager.available()

    @staticmethod
    def _card_bypass_requested(metadata: Optional[Dict[str, Any]]) -> bool:
        """Whether this send asked to skip the card and go out as a message.

        ``hermes_card_bypass`` is the explicit opt-out.  ``non_conversational``
        is the gateway's existing name for a lifecycle/status send, which is
        the same thing by another route — a notice about the session rather
        than a turn of the conversation.

        Tool-progress sends are excluded: those are execution chrome and have
        a home inside the card's collapsible panel.
        """
        if not metadata:
            return False
        if metadata.get(_CARD_PROGRESS_METADATA_KEY):
            return False
        return bool(
            metadata.get(_CARD_BYPASS_METADATA_KEY)
            or metadata.get("non_conversational")
        )

    async def _send_via_card(
        self,
        *,
        chat_id: str,
        content: str,
        reply_to: Optional[str],
        metadata: Optional[Dict[str, Any]],
        owner: str = "main",
        title: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> Optional[SendResult]:
        """Append ``content`` to the live card, or ``None`` to fall back.

        The returned ``message_id`` is a card *block* handle, not a Feishu
        message id: the gateway streams a reply by sending and then editing,
        and ``_edit_via_card`` maps the handle back onto the block so the edit
        rewrites exactly that text.
        """
        if not self._card_output_active():
            return None
        # Transient status notices stay out of the card: they are not part of
        # the answer, and appending them would interleave a heartbeat or an
        # acknowledgement into the text the user is reading.  ``kind`` being
        # set means an internal caller has already decided where this belongs
        # (delegate trace / body), so the opt-out does not apply to it.
        if kind is None and self._card_bypass_requested(metadata):
            return None
        manager = self._card_manager
        resolved_kind = kind or (
            "trace"
            if (metadata or {}).get(_CARD_PROGRESS_METADATA_KEY)
            else "body"
        )
        try:
            delivered = await manager.deliver(
                chat_id=chat_id,
                content=self.format_message(content),
                kind=resolved_kind,
                owner=owner,
                title=title,
                reply_to=reply_to,
                metadata=metadata,
            )
        except Exception:
            logger.warning("[Feishu] card delivery failed; using text/post", exc_info=True)
            return None
        if delivered is None:
            return None
        block_id, _card_message_id = delivered
        return SendResult(success=True, message_id=block_id)

    # -- attachments in card mode -----------------------------------------

    #: Bound on the per-turn ledger.  A delegate foreground loop keeps one
    #: turn open across many exchanges, so this cannot grow per-turn forever.
    _CARD_MEDIA_LEDGER_MAX = 256

    def _card_media_route(
        self, chat_id: str, metadata: Optional[Dict[str, Any]]
    ) -> Optional[str]:
        """The card route for this send, or ``None`` when cards are not in play.

        ``None`` is the backward-compatibility answer: with card output off, or
        outside a turn, every attachment path below becomes a no-op and the
        legacy behaviour is untouched.
        """
        manager = getattr(self, "_card_manager", None)
        if manager is None or not self._card_output_active():
            return None
        try:
            route = manager.resolve_route(chat_id, metadata)
        except Exception:
            return None
        return route if manager.turn_active(route) else None

    @staticmethod
    def _card_media_key(path: str) -> str:
        return os.path.abspath(os.path.expanduser(str(path or "")))

    def _claim_card_media(self, chat_id: str, path: str, metadata) -> bool:
        """Claim ``path`` for this turn; ``False`` when it was already sent.

        One file can reach the uploader from two directions inside a single
        turn: the gateway extracts attachments from the final reply and calls
        the senders itself, while the card-text safety net extracts them from
        text that never passed through that extraction.  Without a shared
        ledger the two together deliver the same file twice.

        The claim happens *before* the upload, not after it: ``_send_with_retry``
        re-sends byte-identical content after a transient failure, so a claim
        that waited for success would upload twice on the first hiccup.
        """
        route = self._card_media_route(chat_id, metadata)
        if route is None:
            return True
        ledger = self._card_media_dispatched.setdefault(route, OrderedDict())
        key = self._card_media_key(path)
        if key in ledger:
            logger.debug("[Feishu] attachment already sent this turn: %s", key)
            return False
        ledger[key] = None
        while len(ledger) > self._CARD_MEDIA_LEDGER_MAX:
            ledger.popitem(last=False)
        return True

    def _release_card_media(self, chat_id: str, path: str, metadata) -> None:
        """Undo a claim whose upload failed, so a later attempt can retry."""
        route = self._card_media_route(chat_id, metadata)
        if route is None:
            return
        ledger = self._card_media_dispatched.get(route)
        if ledger is not None:
            ledger.pop(self._card_media_key(path), None)

    async def _send_media_once(self, *, chat_id: str, path: str, metadata, send):
        """Run ``send`` unless this file already left during this card turn.

        A skip is reported as success: the user has the file, so the caller
        must not render a delivery-failure notice for it.  Outside a card turn
        the ledger is inert and every send goes through untouched.
        """
        if not self._claim_card_media(chat_id, path, metadata):
            return SendResult(success=True)
        result = await send()
        if not getattr(result, "success", False):
            self._release_card_media(chat_id, path, metadata)
        return result

    def _split_card_mode_attachments(
        self,
        *,
        chat_id: str,
        content: str,
        metadata: Optional[Dict[str, Any]],
    ) -> Tuple[str, List[Tuple[str, bool]]]:
        """Take file references out of text that is about to become card body.

        The gateway normally strips attachments out of a reply long before it
        reaches ``send``: ``_process_message_background`` extracts them, sends
        the remaining prose, then uploads each file.  Card mode adds text entry
        points that skip that pass — mid-turn commentary and delegate output go
        straight to ``send`` — and a file reference arriving that way would be
        rendered as a literal path inside the card instead of arriving as a
        file.

        Extraction reuses the gateway's own helpers rather than a local regex,
        so the rule set here is *identical* to the non-card path: the same
        extensions, the same code-fence and inline-code masking, the same
        delivery allowlist.  A reply that merely explains ``MEDIA:`` syntax or
        pastes a path inside a code block still uploads nothing.

        Returns the text to put in the card and the files still owed.
        """
        text = str(content or "")
        if "MEDIA:" not in text and "/" not in text and "\\" not in text:
            return text, []
        if self._card_media_route(chat_id, metadata) is None:
            return text, []
        if self._card_bypass_requested(metadata) or (metadata or {}).get(
            _CARD_PROGRESS_METADATA_KEY
        ):
            # Lifecycle notices and tool chrome are not attachment requests.
            return text, []

        try:
            media, cleaned = self.extract_media(text)
            media = self.filter_media_delivery_paths(media)
            cleaned = self.strip_media_directives_for_display(cleaned)
            bare, cleaned = self.extract_local_files(cleaned)
            bare = self.filter_local_delivery_paths(bare)
        except Exception:
            # Extraction must never cost the user their reply.
            logger.debug("[Feishu] card attachment extraction failed", exc_info=True)
            return text, []

        owed = list(media) + [(path, False) for path in bare]
        if not owed:
            return text, []

        names = "、".join(dict.fromkeys(os.path.basename(p) for p, _ in owed))
        note = self._card_copy.attachment_note.format(names=names)
        cleaned = cleaned.strip()
        body = f"{cleaned}\n\n{note}" if cleaned else note
        return body, owed

    async def _dispatch_card_attachments(
        self,
        *,
        chat_id: str,
        attachments: List[Tuple[str, bool]],
        metadata: Optional[Dict[str, Any]],
    ) -> None:
        """Upload each owed file as its own message beside the card.

        Routing mirrors the gateway's own dispatch — audio to a voice bubble,
        video to a media message, images inline, everything else a file — so a
        deliverable arrives the same way whether card output is on or off.
        """
        for path, is_voice in attachments:
            ext = os.path.splitext(path)[1].lower()
            try:
                if should_send_media_as_audio(self.platform, ext, is_voice=is_voice):
                    result = await self.send_voice(
                        chat_id=chat_id, audio_path=path, metadata=metadata,
                    )
                elif ext in _CARD_ATTACHMENT_VIDEO_EXTS:
                    result = await self.send_video(
                        chat_id=chat_id, video_path=path, metadata=metadata,
                    )
                elif ext in _CARD_ATTACHMENT_IMAGE_EXTS:
                    result = await self.send_image_file(
                        chat_id=chat_id, image_path=path, metadata=metadata,
                    )
                else:
                    result = await self.send_document(
                        chat_id=chat_id, file_path=path, metadata=metadata,
                    )
            except Exception:
                logger.warning(
                    "[Feishu] card attachment upload raised for %s", path, exc_info=True,
                )
                continue
            if not getattr(result, "success", False):
                logger.warning(
                    "[Feishu] card attachment upload failed for %s: %s",
                    path,
                    getattr(result, "error", None),
                )
                await self._notify_media_delivery_failure(
                    chat_id, path, is_voice=is_voice, metadata=metadata,
                )

    async def _edit_via_card(self, message_id: str, content: str) -> Optional[SendResult]:
        """Rewrite a card block.  ``None`` when ``message_id`` is not one."""
        manager = getattr(self, "_card_manager", None)
        if manager is None or not manager.owns_message(message_id):
            return None
        try:
            edited = await manager.edit(
                message_id=message_id, content=self.format_message(content)
            )
        except Exception:
            logger.warning("[Feishu] card edit failed", exc_info=True)
            edited = False
        if edited is None:
            return None
        if not edited:
            # The block's card was already sealed (turn finished, or the body
            # rolled onto a continuation card).  Its text is on screen either
            # way, and reporting failure here would make the gateway re-send
            # the same text as a brand-new bubble.
            logger.debug("[Feishu] card block %s is sealed; edit ignored", message_id)
        return SendResult(success=True, message_id=message_id)

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        """Retract a card block.

        Feishu has no message-recall API for the bot, so this stays False for
        real messages — the base class default.  A card block is different: it
        is text inside a card this adapter still owns, and the stream consumer
        deletes a superseded preview after re-sending the finished answer.
        Without this the card would show the answer twice.
        """
        manager = getattr(self, "_card_manager", None)
        if manager is None or not manager.owns_message(message_id):
            return await super().delete_message(chat_id, message_id)
        try:
            removed = await manager.remove(message_id=message_id)
        except Exception:
            logger.warning("[Feishu] card block retraction failed", exc_info=True)
            return False
        return bool(removed)

    def _delegate_card_streamed_owners(self) -> set:
        """Owners that streamed body text during the current exchange."""
        streamed = getattr(self, "_delegate_card_streamed", None)
        if streamed is None:
            streamed = set()
            self._delegate_card_streamed = streamed
        return streamed

    def _delegate_card_lock(self, owner: str) -> asyncio.Lock:
        """Serialize card writes for one delegate.

        Every delegate event is scheduled as its own task, so without this an
        opening burst of deltas each opens its own body block.
        """
        locks = getattr(self, "_delegate_card_locks", None)
        if locks is None:
            locks = {}
            self._delegate_card_locks = locks
        lock = locks.get(owner)
        if lock is None:
            lock = asyncio.Lock()
            locks[owner] = lock
        return lock

    def _card_delegate_owner(self, session_id: Any) -> str:
        """Owner tag for a delegated remote agent.

        Distinct A2A context ids get distinct owners, so two delegations in
        one turn land on two cards; the same delegation keeps writing to its
        own card across turns of a foreground loop.
        """
        token = str(session_id or "").strip()
        return f"delegate:{token}" if token else "delegate:anonymous"

    async def handle_delegate_card_event(
        self,
        *,
        chat_id: str,
        metadata: Optional[Dict[str, Any]],
        owner: str,
        agent_name: Optional[str],
        event_type: str,
        content: str,
    ) -> bool:
        """Render one delegate stream event into that delegate's own card.

        Returns False when cards are unavailable, which leaves the caller on
        the historical per-event message path.
        """
        if not self._card_output_active():
            return False
        manager = self._card_manager
        title = manager.title_for(owner, agent_name=agent_name)
        states = getattr(self, "_delegate_card_blocks", None)
        if states is None:
            states = {}
            self._delegate_card_blocks = states

        async def _append(text: str, kind: str) -> bool:
            result = await self._send_via_card(
                chat_id=chat_id,
                content=text,
                reply_to=None,
                metadata=metadata,
                owner=owner,
                title=title,
                kind=kind,
            )
            return result is not None and result.success

        if event_type == "ai_delta":
            if not content:
                return True
            # Accumulate BEFORE awaiting anything.  This runs to completion
            # without yielding, so concurrent delta tasks cannot interleave
            # here and every render below writes the full accumulation — no
            # fragment is lost or reordered, and each update stays a prefix
            # superset of the last, which is what animates the typewriter.
            state = states.get(owner)
            if state is None:
                state = {"block_id": None, "text": ""}
                states[owner] = state
            state["text"] += content
            self._delegate_card_streamed_owners().add(owner)

            async with self._delegate_card_lock(owner):
                # Re-read under the lock: a task that queued behind the one
                # that opened the block must extend it, not open another.
                live = states.get(owner)
                if live is not state:
                    # A segment boundary (tool call / turn end) landed while
                    # this task waited; the text it carried is already
                    # rendered, so there is nothing left to do.
                    return True
                if live["block_id"] is None:
                    result = await self._send_via_card(
                        chat_id=chat_id, content=live["text"], reply_to=None,
                        metadata=metadata, owner=owner, title=title, kind="body",
                    )
                    if result is None or not result.success:
                        states.pop(owner, None)
                        return False
                    live["block_id"] = result.message_id
                    return True
                edited = await self._edit_via_card(live["block_id"], live["text"])
                return edited is not None and edited.success

        if event_type == "ai":
            async with self._delegate_card_lock(owner):
                # Turn-final for this delegate.  Deltas already streamed the
                # text into the body, so appending the final response on top
                # of them would show the answer twice — only a delegate that
                # never streamed at all needs it appended here.
                states.pop(owner, None)
                streamed = self._delegate_card_streamed_owners()
                if content and owner not in streamed:
                    await _append(content, "body")
                streamed.discard(owner)
                # Seal the card: one card per exchange with the remote agent.
                # A foreground loop holds a single A2A context for all its
                # turns, so the owner tag never changes and nothing else would
                # close it — every follow-up answer would pile into the first
                # card.
                await manager.close_owner(chat_id=chat_id, metadata=metadata, owner=owner)
            return True

        if event_type == "tool_call":
            async with self._delegate_card_lock(owner):
                states.pop(owner, None)  # tool boundary ends the text segment
                return await _append(
                    _FeishuDelegateOutputAdapter._format_tool_call(content), "trace"
                )

        if event_type == "status":
            # Transport plumbing — "entered foreground loop", "return to main"
            # and friends describe the delegation machinery, not the user's
            # task.  The agent narrates the outcome itself (the a2a tool hands
            # it loop_exit_reason and the final response), so swallow these
            # rather than putting loop bookkeeping in the chat.  Reported as
            # handled so the legacy message path does not render them either.
            logger.debug("[Feishu] dropping delegate status in card mode: %s", content)
            return True

        if event_type == "error":
            async with self._delegate_card_lock(owner):
                states.pop(owner, None)
                return await _append(f"⚠️ `delegate error`: {content}", "body")

        return False

    async def send_voice(
        self,
        chat_id: str,
        audio_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        """Send audio to Feishu as a file attachment plus optional caption."""
        return await self._send_media_once(
            chat_id=chat_id,
            path=audio_path,
            metadata=metadata,
            send=lambda: self._send_uploaded_file_message(
                chat_id=chat_id,
                file_path=audio_path,
                reply_to=reply_to,
                metadata=metadata,
                caption=caption,
                outbound_message_type="audio",
            ),
        )

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        """Send a document/file attachment to Feishu."""
        return await self._send_media_once(
            chat_id=chat_id,
            path=file_path,
            metadata=metadata,
            send=lambda: self._send_uploaded_file_message(
                chat_id=chat_id,
                file_path=file_path,
                reply_to=reply_to,
                metadata=metadata,
                caption=caption,
                file_name=file_name,
            ),
        )

    async def send_video(
        self,
        chat_id: str,
        video_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        """Send a video file to Feishu."""
        return await self._send_media_once(
            chat_id=chat_id,
            path=video_path,
            metadata=metadata,
            send=lambda: self._send_uploaded_file_message(
                chat_id=chat_id,
                file_path=video_path,
                reply_to=reply_to,
                metadata=metadata,
                caption=caption,
                outbound_message_type="media",
            ),
        )

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        """Send a local image file to Feishu."""
        return await self._send_media_once(
            chat_id=chat_id,
            path=image_path,
            metadata=metadata,
            send=lambda: self._send_image_file_impl(
                chat_id=chat_id,
                image_path=image_path,
                caption=caption,
                reply_to=reply_to,
                metadata=metadata,
            ),
        )

    async def _send_image_file_impl(
        self,
        *,
        chat_id: str,
        image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        if not self._client:
            return SendResult(success=False, error="Not connected")
        if not os.path.exists(image_path):
            return SendResult(success=False, error=f"Image file not found: {image_path}")

        try:
            import io as _io
            with open(image_path, "rb") as f:
                image_bytes = f.read()
            # Wrap in BytesIO so lark SDK's MultipartEncoder can read .name and .tell()
            image_file = _io.BytesIO(image_bytes)
            image_file.name = os.path.basename(image_path)
            body = self._build_image_upload_body(
                image_type=_FEISHU_IMAGE_UPLOAD_TYPE,
                image=image_file,
            )
            request = self._build_image_upload_request(body)
            upload_response = await self._run_blocking(self._client.im.v1.image.create, request)
            image_key = self._extract_response_field(upload_response, "image_key")
            if not image_key:
                return self._response_error_result(
                    upload_response,
                    default_message="image upload failed",
                    override_error="Feishu image upload missing image_key",
                )

            if caption:
                post_payload = self._build_media_post_payload(
                    caption=caption,
                    media_tag={"tag": "img", "image_key": image_key},
                )
                message_response = await self._send_attachment_message(
                    chat_id=chat_id,
                    msg_type="post",
                    payload=post_payload,
                    reply_to=reply_to,
                    metadata=metadata,
                )
            else:
                message_response = await self._send_attachment_message(
                    chat_id=chat_id,
                    msg_type="image",
                    payload=json.dumps({"image_key": image_key}, ensure_ascii=False),
                    reply_to=reply_to,
                    metadata=metadata,
                )
            return self._finalize_send_result(message_response, "image send failed")
        except Exception as exc:
            logger.error("[Feishu] Failed to send image %s: %s", image_path, exc, exc_info=True)
            return SendResult(success=False, error=str(exc))

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        """Feishu bot API does not expose a typing indicator."""
        return None

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Download a remote image then send it through the native Feishu image flow."""
        try:
            image_path = await self._download_remote_image(image_url)
        except Exception as exc:
            logger.error("[Feishu] Failed to download image %s: %s", image_url, exc, exc_info=True)
            return await super().send_image(
                chat_id=chat_id,
                image_url=image_url,
                caption=caption,
                reply_to=reply_to,
                metadata=metadata,
            )
        return await self.send_image_file(
            chat_id=chat_id,
            image_path=image_path,
            caption=caption,
            reply_to=reply_to,
            metadata=metadata,
        )

    async def send_animation(
        self,
        chat_id: str,
        animation_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Feishu has no native GIF bubble; degrade to a downloadable file."""
        try:
            file_path, file_name = await self._download_remote_document(
                animation_url,
                default_ext=".gif",
                preferred_name="animation.gif",
            )
        except Exception as exc:
            logger.error("[Feishu] Failed to download animation %s: %s", animation_url, exc, exc_info=True)
            return await super().send_animation(
                chat_id=chat_id,
                animation_url=animation_url,
                caption=caption,
                reply_to=reply_to,
                metadata=metadata,
            )
        degraded_caption = f"[GIF downgraded to file]\n{caption}" if caption else "[GIF downgraded to file]"
        return await self.send_document(
            chat_id=chat_id,
            file_path=file_path,
            file_name=file_name,
            caption=degraded_caption,
            reply_to=reply_to,
            metadata=metadata,
        )

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """Return real chat metadata from Feishu when available."""
        fallback = {
            "chat_id": chat_id,
            "name": chat_id,
            "type": "dm",
        }
        if not self._client:
            return fallback

        cached = self._chat_info_cache.get(chat_id)
        if cached is not None:
            return dict(cached)

        try:
            request = self._build_get_chat_request(chat_id)
            response = await self._run_blocking(self._client.im.v1.chat.get, request)
            if not response or getattr(response, "success", lambda: False)() is False:
                code = getattr(response, "code", "unknown")
                msg = getattr(response, "msg", "chat lookup failed")
                logger.warning("[Feishu] Failed to get chat info for %s: [%s] %s", chat_id, code, msg)
                return fallback

            data = getattr(response, "data", None)
            raw_chat_type = str(getattr(data, "chat_type", "") or "").strip().lower()
            info = {
                "chat_id": chat_id,
                "name": str(getattr(data, "name", None) or chat_id),
                "type": self._map_chat_type(raw_chat_type),
                "raw_type": raw_chat_type or None,
            }
            self._chat_info_cache[chat_id] = info
            return dict(info)
        except Exception:
            logger.warning("[Feishu] Failed to get chat info for %s", chat_id, exc_info=True)
            return fallback

    def format_message(self, content: str) -> str:
        """Feishu text messages are plain text by default."""
        return content.strip()

    # =========================================================================
    # Inbound event handlers
    # =========================================================================

    def _on_message_event(self, data: Any) -> None:
        """Normalize Feishu inbound events into MessageEvent.

        Called by the lark_oapi SDK's event dispatcher on a background thread.
        If the adapter loop is not currently accepting callbacks (brief window
        during startup/restart or network-flap reconnect), the event is queued
        for replay instead of dropped.
        """
        loop = self._loop
        if not self._loop_accepts_callbacks(loop):
            start_drainer = self._enqueue_pending_inbound_event(data)
            if start_drainer:
                threading.Thread(
                    target=self._drain_pending_inbound_events,
                    name="feishu-pending-inbound-drainer",
                    daemon=True,
                ).start()
            return
        self._submit_on_loop(loop, self._handle_message_event_data(data))

    def _enqueue_pending_inbound_event(self, data: Any) -> bool:
        """Append an event to the pending-inbound queue.

        Returns True if the caller should spawn a drainer thread (no drainer
        currently scheduled), False if a drainer is already running and will
        pick up the new event on its next pass.
        """
        with self._pending_inbound_lock:
            if len(self._pending_inbound_events) >= self._pending_inbound_max_depth:
                # Queue full — drop the oldest to make room. This happens only
                # if the loop stays unavailable for an extended period AND the
                # WS keeps firing callbacks. Still better than silent drops.
                dropped = self._pending_inbound_events.pop(0)
                try:
                    event = getattr(dropped, "event", None)
                    message = getattr(event, "message", None)
                    message_id = str(getattr(message, "message_id", "") or "unknown")
                except Exception:
                    message_id = "unknown"
                logger.error(
                    "[Feishu] Pending-inbound queue full (%d); dropped oldest event %s",
                    self._pending_inbound_max_depth,
                    message_id,
                )
            self._pending_inbound_events.append(data)
            depth = len(self._pending_inbound_events)
            should_start = not self._pending_drain_scheduled
            if should_start:
                self._pending_drain_scheduled = True
        logger.warning(
            "[Feishu] Queued inbound event for replay (loop not ready, queue depth=%d)",
            depth,
        )
        return should_start

    def _drain_pending_inbound_events(self) -> None:
        """Replay queued inbound events once the adapter loop is ready.

        Runs in a dedicated daemon thread. Polls ``_running`` and
        ``_loop_accepts_callbacks`` until events can be dispatched or the
        adapter shuts down. A single drainer handles the entire queue;
        concurrent ``_on_message_event`` calls just append.
        """
        poll_interval = 0.25
        max_wait_seconds = 120.0  # safety cap: drop queue after 2 minutes
        waited = 0.0
        try:
            while True:
                if not getattr(self, "_running", True):
                    # Adapter shutting down — drop queued events rather than
                    # holding them against a closed loop.
                    with self._pending_inbound_lock:
                        dropped = len(self._pending_inbound_events)
                        self._pending_inbound_events.clear()
                    if dropped:
                        logger.warning(
                            "[Feishu] Dropped %d queued inbound event(s) during shutdown",
                            dropped,
                        )
                    return
                loop = self._loop
                if self._loop_accepts_callbacks(loop):
                    with self._pending_inbound_lock:
                        batch = self._pending_inbound_events[:]
                        self._pending_inbound_events.clear()
                    if not batch:
                        # Queue emptied between check and grab; done.
                        with self._pending_inbound_lock:
                            if not self._pending_inbound_events:
                                return
                        continue
                    dispatched = 0
                    requeue: List[Any] = []
                    for event in batch:
                        if self._submit_on_loop(
                            loop, self._handle_message_event_data(event)
                        ):
                            dispatched += 1
                        else:
                            # Loop closed/unavailable — requeue and poll again.
                            requeue.append(event)
                    if requeue:
                        with self._pending_inbound_lock:
                            self._pending_inbound_events[:0] = requeue
                    if dispatched:
                        logger.info(
                            "[Feishu] Replayed %d queued inbound event(s)",
                            dispatched,
                        )
                    if not requeue:
                        # Successfully drained; check if more arrived while
                        # we were dispatching and exit if not.
                        with self._pending_inbound_lock:
                            if not self._pending_inbound_events:
                                return
                    # More events queued or requeue pending — loop again.
                    continue
                if waited >= max_wait_seconds:
                    with self._pending_inbound_lock:
                        dropped = len(self._pending_inbound_events)
                        self._pending_inbound_events.clear()
                    logger.error(
                        "[Feishu] Adapter loop unavailable for %.0fs; "
                        "dropped %d queued inbound event(s)",
                        max_wait_seconds,
                        dropped,
                    )
                    return
                time.sleep(poll_interval)
                waited += poll_interval
        finally:
            with self._pending_inbound_lock:
                self._pending_drain_scheduled = False

    async def _handle_message_event_data(self, data: Any) -> None:
        """Shared inbound message handling for websocket and webhook transports."""
        event = getattr(data, "event", None)
        message = getattr(event, "message", None)
        sender = getattr(event, "sender", None)
        if not message or not sender or not getattr(sender, "sender_id", None):
            logger.debug("[Feishu] Dropping malformed inbound event: missing message/sender")
            return

        message_id = getattr(message, "message_id", None)
        if not message_id or self._is_duplicate(message_id):
            logger.debug("[Feishu] Dropping duplicate/missing message_id: %s", message_id)
            return

        reason = self._admit(sender, message)
        if reason is not None:
            logger.debug("[Feishu] dropping inbound event: %s", reason)
            return

        chat_type = getattr(message, "chat_type", "p2p")
        await self._process_inbound_message(
            data=data,
            message=message,
            sender_id=getattr(sender, "sender_id", None),
            chat_type=chat_type,
            message_id=message_id,
            is_bot=_is_bot_sender(sender),
        )

    def _on_message_read_event(self, data: P2ImMessageMessageReadV1) -> None:
        """Ignore read-receipt events that Hermes does not act on."""
        event = getattr(data, "event", None)
        message = getattr(event, "message", None)
        message_id = getattr(message, "message_id", None) or ""
        logger.debug("[Feishu] Ignoring message_read event: %s", message_id)

    def _on_bot_added_to_chat(self, data: Any) -> None:
        """Handle bot being added to a group chat."""
        event = getattr(data, "event", None)
        chat_id = str(getattr(event, "chat_id", "") or "")
        logger.info("[Feishu] Bot added to chat: %s", chat_id)
        self._chat_info_cache.pop(chat_id, None)

    def _on_bot_removed_from_chat(self, data: Any) -> None:
        """Handle bot being removed from a group chat."""
        event = getattr(data, "event", None)
        chat_id = str(getattr(event, "chat_id", "") or "")
        logger.info("[Feishu] Bot removed from chat: %s", chat_id)
        self._chat_info_cache.pop(chat_id, None)

    def _on_p2p_chat_entered(self, data: Any) -> None:
        logger.debug("[Feishu] User entered P2P chat with bot")

    def _on_message_recalled(self, data: Any) -> None:
        logger.debug("[Feishu] Message recalled by user")

    def _on_drive_comment_event(self, data: Any) -> None:
        """Handle drive document comment notification (drive.notice.comment_add_v1).

        Delegates to :mod:`gateway.platforms.feishu_comment` for parsing,
        logging, and reaction.  Scheduling follows the same
        ``run_coroutine_threadsafe`` pattern used by ``_on_message_event``.
        """
        from plugins.platforms.feishu.feishu_comment import handle_drive_comment_event

        loop = self._loop
        if not self._loop_accepts_callbacks(loop):
            logger.warning("[Feishu] Dropping drive comment event before adapter loop is ready")
            return
        self._submit_on_loop(
            loop,
            handle_drive_comment_event(self._client, data, self_open_id=self._bot_open_id),
        )

    def _on_meeting_invited_event(self, data: Any) -> None:
        """Handle VC bot meeting invitation notification (vc.bot.meeting_invited_v1)."""
        from plugins.platforms.feishu.feishu_meeting_invite import handle_meeting_invited_event

        loop = self._loop
        if not self._loop_accepts_callbacks(loop):
            logger.warning("[Feishu] Dropping meeting invite event before adapter loop is ready")
            return
        self._submit_on_loop(loop, handle_meeting_invited_event(self, data))

    def _on_reaction_event(self, event_type: str, data: Any) -> None:
        """Route user reactions on bot messages as synthetic text events."""
        event = getattr(data, "event", None)
        message_id = str(getattr(event, "message_id", "") or "")
        operator_type = str(getattr(event, "operator_type", "") or "")
        reaction_type_obj = getattr(event, "reaction_type", None)
        emoji_type = str(getattr(reaction_type_obj, "emoji_type", "") or "")
        action = "added" if "created" in event_type else "removed"
        logger.debug(
            "[Feishu] Reaction %s on message %s (operator_type=%s, emoji=%s)",
            action,
            message_id,
            operator_type,
            emoji_type,
        )
        # Drop bot/app-origin reactions to break the feedback loop from our
        # own lifecycle reactions. A human reacting with the same emoji (e.g.
        # clicking Typing on a bot message) is still routed through.
        loop = self._loop
        if (
            operator_type in {"bot", "app"}
            or not message_id
            or loop is None
            or bool(getattr(loop, "is_closed", lambda: False)())
        ):
            return
        self._submit_on_loop(loop, self._handle_reaction_event(event_type, data))

    def _on_card_action_trigger(self, data: Any) -> Any:
        """Handle card-action callback from the Feishu SDK (synchronous).

        For approval actions: parses the event once, returns the resolved card
        inline (the only reliable way to sync all clients), and schedules a
        lightweight async method to actually unblock the agent.

        For other card actions: delegates to ``_handle_card_action_event``.
        """
        loop = self._loop
        if not self._loop_accepts_callbacks(loop):
            logger.warning("[Feishu] Dropping card action before adapter loop is ready")
            return P2CardActionTriggerResponse() if P2CardActionTriggerResponse else None

        event = getattr(data, "event", None)
        action = getattr(event, "action", None)
        action_value = getattr(action, "value", {}) or {}
        hermes_action = action_value.get("hermes_action") if isinstance(action_value, dict) else None
        update_prompt_action = (
            action_value.get("hermes_update_prompt_action")
            if isinstance(action_value, dict) else None
        )
        clarify_action = (
            action_value.get("hermes_clarify_action")
            if isinstance(action_value, dict) else None
        )

        delegate_kind = action_value.get("hermes_delegate_kind") if isinstance(action_value, dict) else None
        if delegate_kind:
            token = str(getattr(event, "token", "") or "")
            if token and self._is_card_action_duplicate(token):
                return self._empty_card_action_response()
            return self._handle_delegate_card_action(
                event=event,
                action_value=action_value,
                loop=loop,
            )

        if hermes_action:
            return self._handle_approval_card_action(event=event, action_value=action_value, loop=loop)
        if update_prompt_action:
            return self._handle_update_prompt_card_action(
                event=event,
                action_value=action_value,
                loop=loop,
            )
        if clarify_action:
            token = str(getattr(event, "token", "") or "")
            if token and self._is_card_action_duplicate(token):
                logger.debug("[Feishu] Dropping duplicate clarify card action token: %s", token)
                return self._empty_card_action_response()
            return self._handle_clarify_card_action(
                event=event,
                action_value=action_value,
                loop=loop,
            )

        self._submit_on_loop(loop, self._handle_card_action_event(data))
        if P2CardActionTriggerResponse is None:
            return None
        return P2CardActionTriggerResponse()

    @staticmethod
    def _loop_accepts_callbacks(loop: Any) -> bool:
        """Return True when the adapter loop can accept thread-safe submissions."""
        return loop is not None and not bool(getattr(loop, "is_closed", lambda: False)())

    def _submit_on_loop(self, loop: Any, coro: Any) -> bool:
        """Schedule background work on the adapter loop with shared failure logging."""
        from agent.async_utils import safe_schedule_threadsafe
        future = safe_schedule_threadsafe(
            coro, loop,
            logger=logger,
            log_message="[Feishu] Failed to schedule background callback work",
            log_level=logging.WARNING,
        )
        if future is None:
            return False
        future.add_done_callback(self._log_background_failure)
        return True

    def _is_interactive_operator_authorized(self, open_id: str) -> bool:
        """Return whether this card-action operator may answer gated prompts."""
        normalized = str(open_id or "").strip()
        if not normalized:
            return False
        allowed_ids = set(self._admins) | set(self._allowed_group_users)
        if not allowed_ids:
            return True
        return "*" in allowed_ids or normalized in allowed_ids

    def _empty_card_action_response(self) -> Any:
        return P2CardActionTriggerResponse() if P2CardActionTriggerResponse else None

    def _delegate_card_response(self, title: str, content: str) -> Any:
        return self._build_card_action_response({
            "config": {"wide_screen_mode": True},
            "header": {"title": {"content": title, "tag": "plain_text"}, "template": "blue"},
            "elements": [{"tag": "markdown", "content": content[:3000]}],
        })

    def _handle_delegate_card_action(self, *, event: Any, action_value: Dict[str, Any], loop: Any) -> Any:
        interaction_id = str(action_value.get("interaction_id") or "")
        state = self._delegate_interactions.get(interaction_id)
        if not interaction_id or state is None or state.get("resolved"):
            return self._empty_card_action_response()
        callback_chat_id = str(getattr(getattr(event, "context", None), "open_chat_id", "") or "")
        if callback_chat_id and callback_chat_id != str(state.get("chat_id") or ""):
            return self._empty_card_action_response()
        operator = getattr(event, "operator", None)
        open_id = str(getattr(operator, "open_id", "") or "")
        operator_user_id = str(getattr(operator, "user_id", "") or "")
        # ``SessionSource.user_id`` is tenant-scoped when Feishu provides it,
        # while card callbacks identify the operator with both the
        # app-scoped ``open_id`` and (when available) the tenant-scoped
        # ``user_id``.  Accept either representation here; comparing the
        # stored source id only to ``open_id`` rejects legitimate delegate
        # clicks before the A2A responder can run.
        stored_user_id = str(state.get("user_id") or "")
        if stored_user_id and stored_user_id not in {open_id, operator_user_id}:
            logger.warning(
                "[Feishu] Delegate callback user mismatch for interaction %s",
                interaction_id,
            )
            return self._empty_card_action_response()
        sender_id = SimpleNamespace(open_id=open_id, user_id=operator_user_id)
        if not self._allow_group_message(sender_id, str(state.get("chat_id") or ""), is_bot=False) or not self._is_interactive_operator_authorized(open_id):
            return self._empty_card_action_response()

        token = str(action_value.get("token") or action_value.get("choice") or "")
        if state.get("kind") == "approval":
            if token not in {"once", "session", "always", "deny"}:
                return self._empty_card_action_response()
            responder = state.get("responder")
            if not callable(responder) or not responder(interaction_id, "approval", token):
                return self._delegate_card_response("Delegate approval", "The remote response channel is unavailable.")
            state["resolved"] = True
            return self._delegate_card_response("Delegate approval resolved", f"{token} by {open_id}")

        if token == "other":
            state["awaiting_text"] = True
            return self._delegate_card_response("Delegate clarification", f"Waiting for a typed answer from {open_id}.")
        if token == "submit":
            selected = list(state.get("selected") or [])
            if not selected:
                return self._delegate_card_response("Delegate clarification", "Select at least one option first.")
            value_to_send: Any = selected
        else:
            try:
                choice = list(state.get("choices") or [])[int(token)]
            except (ValueError, TypeError, IndexError):
                return self._empty_card_action_response()
            if state.get("multi_select"):
                selected = list(state.get("selected") or [])
                if choice in selected:
                    selected.remove(choice)
                else:
                    selected.append(choice)
                state["selected"] = selected
                return self._delegate_card_response("Delegate clarification", "Selected: " + (", ".join(selected) if selected else "none"))
            value_to_send = choice
        responder = state.get("responder")
        if not callable(responder) or not responder(interaction_id, "clarify", value_to_send):
            return self._delegate_card_response("Delegate clarification", "The remote response channel is unavailable.")
        state["resolved"] = True
        state["awaiting_text"] = False
        return self._delegate_card_response("Delegate clarification resolved", f"Response sent by {open_id}.")

    def _handle_clarify_card_action(self, *, event: Any, action_value: Dict[str, Any], loop: Any) -> Any:
        """Validate and resolve a Feishu clarify card without routing a new turn."""
        clarify_id = str(action_value.get("clarify_id", "") or "")
        state = self._clarify_choices.get(clarify_id)
        if not clarify_id or state is None:
            logger.debug("[Feishu] Unknown or resolved clarify action: %s", clarify_id or "<missing>")
            return self._empty_card_action_response()

        callback_chat_id = str(getattr(getattr(event, "context", None), "open_chat_id", "") or "")
        if callback_chat_id and callback_chat_id != str(state.get("chat_id", "") or ""):
            logger.warning("[Feishu] Clarify callback chat mismatch for %s", clarify_id)
            return self._empty_card_action_response()
        operator = getattr(event, "operator", None)
        open_id = str(getattr(operator, "open_id", "") or "")
        sender_id = SimpleNamespace(
            open_id=open_id,
            user_id=str(getattr(operator, "user_id", "") or ""),
        )
        if (
            not self._allow_group_message(sender_id, str(state.get("chat_id", "") or ""), is_bot=False)
            or not self._is_interactive_operator_authorized(open_id)
        ):
            logger.warning("[Feishu] Unauthorized clarify click by %s", open_id or "<unknown>")
            return self._empty_card_action_response()

        user_name = self._get_cached_sender_name(open_id) or open_id
        index = action_value.get("index")
        if index == "other":
            try:
                from tools.clarify_gateway import mark_awaiting_text

                if not mark_awaiting_text(clarify_id):
                    return self._empty_card_action_response()
            except Exception:
                logger.exception("[Feishu] Failed to mark clarify %s for typed input", clarify_id)
                return self._empty_card_action_response()
            self._clarify_choices.pop(clarify_id, None)
            return self._build_card_action_response(
                self._build_resolved_clarify_card(choice=None, user_name=user_name)
            )

        try:
            choice_text = list(state.get("choices") or [])[int(index)]
        except (IndexError, TypeError, ValueError):
            logger.warning("[Feishu] Invalid clarify choice index for %s: %r", clarify_id, index)
            return self._empty_card_action_response()
        # Retire the adapter-local state before returning the updated card.
        # Card callbacks can arrive again before the scheduled resolver gets a
        # chance to run; keeping it until then would allow two distinct tokens
        # to resolve the same clarify request.
        self._clarify_choices.pop(clarify_id, None)
        if not self._submit_on_loop(
            loop,
            self._resolve_feishu_clarify(clarify_id=clarify_id, response=str(choice_text)),
        ):
            self._clarify_choices[clarify_id] = state
            return self._empty_card_action_response()
        return self._build_card_action_response(
            self._build_resolved_clarify_card(choice=str(choice_text), user_name=user_name)
        )

    @staticmethod
    def _build_card_action_response(card_data: Dict[str, Any]) -> Any:
        if P2CardActionTriggerResponse is None:
            return None
        response = P2CardActionTriggerResponse()
        if CallBackCard is not None:
            card = CallBackCard()
            card.type = "raw"
            card.data = card_data
            response.card = card
        return response

    async def _resolve_feishu_clarify(self, *, clarify_id: str, response: str) -> None:
        """Resolve a selected choice after its adapter-local state is retired."""
        try:
            from tools.clarify_gateway import resolve_gateway_clarify

            resolve_gateway_clarify(clarify_id, response)
        except Exception:
            logger.exception("[Feishu] Failed to resolve clarify %s", clarify_id)

    def _handle_approval_card_action(self, *, event: Any, action_value: Dict[str, Any], loop: Any) -> Any:
        """Schedule approval resolution and build the synchronous callback response."""
        approval_id = action_value.get("approval_id")
        if approval_id is None:
            logger.debug("[Feishu] Card action missing approval_id, ignoring")
            return P2CardActionTriggerResponse() if P2CardActionTriggerResponse else None
        state = self._approval_state.get(approval_id)
        if not state:
            logger.debug("[Feishu] Approval %s already resolved or unknown", approval_id)
            return P2CardActionTriggerResponse() if P2CardActionTriggerResponse else None
        choice = _APPROVAL_CHOICE_MAP.get(action_value.get("hermes_action"), "deny")

        operator = getattr(event, "operator", None)
        open_id = str(getattr(operator, "open_id", "") or "")
        sender_id = SimpleNamespace(open_id=open_id, user_id=str(getattr(operator, "user_id", "") or ""))
        if not self._allow_group_message(sender_id, state.get("chat_id", ""), is_bot=False):
            logger.warning("[Feishu] Unauthorized approval click by %s", open_id or "<unknown>")
            return P2CardActionTriggerResponse() if P2CardActionTriggerResponse else None

        callback_chat_id = str(getattr(getattr(event, "context", None), "open_chat_id", "") or "")
        expected_chat_id = str(state.get("chat_id", "") or "")
        if callback_chat_id and expected_chat_id and callback_chat_id != expected_chat_id:
            logger.warning(
                "[Feishu] Approval callback chat mismatch for %s (expected=%s, got=%s)",
                approval_id,
                expected_chat_id,
                callback_chat_id,
            )
            return P2CardActionTriggerResponse() if P2CardActionTriggerResponse else None

        user_name = self._get_cached_sender_name(open_id) or open_id

        chat_context = getattr(event, "context", None)
        chat_id = str(getattr(chat_context, "open_chat_id", "") or "")
        if not self._submit_on_loop(
            loop,
            self._resolve_approval(
                approval_id=approval_id,
                choice=choice,
                user_name=user_name,
                open_id=open_id,
                chat_id=chat_id,
            ),
        ):
            return P2CardActionTriggerResponse() if P2CardActionTriggerResponse else None

        if P2CardActionTriggerResponse is None:
            return None
        response = P2CardActionTriggerResponse()
        if CallBackCard is not None:
            card = CallBackCard()
            card.type = "raw"
            card.data = self._build_resolved_approval_card(choice=choice, user_name=user_name)
            response.card = card
        return response

    def _handle_update_prompt_card_action(self, *, event: Any, action_value: Dict[str, Any], loop: Any) -> Any:
        """Schedule update prompt resolution and build the synchronous callback response."""
        prompt_id = action_value.get("update_prompt_id")
        if prompt_id is None:
            logger.debug("[Feishu] Card action missing update_prompt_id, ignoring")
            return P2CardActionTriggerResponse() if P2CardActionTriggerResponse else None
        state = self._update_prompt_state.get(prompt_id)
        if not state:
            logger.debug("[Feishu] Update prompt %s already resolved or unknown", prompt_id)
            return P2CardActionTriggerResponse() if P2CardActionTriggerResponse else None

        answer = str(action_value.get("hermes_update_prompt_action", "") or "").strip().lower()
        if answer not in {"y", "n"}:
            logger.debug("[Feishu] Card action has invalid update prompt answer=%r", answer)
            return P2CardActionTriggerResponse() if P2CardActionTriggerResponse else None

        operator = getattr(event, "operator", None)
        open_id = str(getattr(operator, "open_id", "") or "")
        sender_id = SimpleNamespace(open_id=open_id, user_id=str(getattr(operator, "user_id", "") or ""))
        if not self._allow_group_message(sender_id, state.get("chat_id", ""), is_bot=False):
            logger.warning("[Feishu] Unauthorized update prompt click by %s", open_id or "<unknown>")
            return P2CardActionTriggerResponse() if P2CardActionTriggerResponse else None

        callback_chat_id = str(getattr(getattr(event, "context", None), "open_chat_id", "") or "")
        expected_chat_id = str(state.get("chat_id", "") or "")
        if callback_chat_id and expected_chat_id and callback_chat_id != expected_chat_id:
            logger.warning(
                "[Feishu] Update prompt callback chat mismatch for %s (expected=%s, got=%s)",
                prompt_id,
                expected_chat_id,
                callback_chat_id,
            )
            return P2CardActionTriggerResponse() if P2CardActionTriggerResponse else None

        user_name = self._get_cached_sender_name(open_id) or open_id
        if not self._submit_on_loop(
            loop,
            self._resolve_update_prompt(
                prompt_id,
                answer,
                user_name,
                open_id=open_id,
                chat_id=callback_chat_id,
            ),
        ):
            return P2CardActionTriggerResponse() if P2CardActionTriggerResponse else None

        if P2CardActionTriggerResponse is None:
            return None
        response = P2CardActionTriggerResponse()
        if CallBackCard is not None:
            card = CallBackCard()
            card.type = "raw"
            card.data = self._build_resolved_update_prompt_card(answer=answer, user_name=user_name)
            response.card = card
        return response

    async def _resolve_approval(
        self,
        approval_id: Any,
        choice: str,
        user_name: str,
        *,
        open_id: str = "",
        chat_id: str = "",
    ) -> None:
        """Pop approval state and unblock the waiting agent thread."""
        state = self._approval_state.get(approval_id)
        if not state:
            logger.debug("[Feishu] Approval %s already resolved or unknown", approval_id)
            return
        if not self._is_interactive_operator_authorized(open_id):
            logger.warning("[Feishu] Unauthorized approval click by %s for approval %s", open_id or "<unknown>", approval_id)
            return
        expected_chat_id = str(state.get("chat_id", "") or "")
        if expected_chat_id and chat_id and expected_chat_id != chat_id:
            logger.warning(
                "[Feishu] Approval %s chat mismatch (expected=%s, got=%s)",
                approval_id, expected_chat_id, chat_id,
            )
            return
        state = self._approval_state.pop(approval_id, None)
        if not state:
            logger.debug("[Feishu] Approval %s already resolved while validating callback", approval_id)
            return
        try:
            from tools.approval import resolve_gateway_approval
            count = resolve_gateway_approval(state["session_key"], choice)
            logger.info(
                "Feishu button resolved %d approval(s) for session %s (choice=%s, user=%s)",
                count, state["session_key"], choice, user_name,
            )
            if not count and choice != "deny":
                # The card was already updated synchronously to "Approved" by
                # the callback response, but nothing was waiting — the wait
                # already timed out (fail-closed deny) or was resolved via
                # /approve. Correct the record so the user doesn't believe
                # the command ran.
                _chat = str(state.get("chat_id", "") or chat_id or "")
                if _chat:
                    try:
                        await self.send(
                            _chat,
                            "⌛ That approval had already expired — the command "
                            "was not run (it timed out or was resolved elsewhere).",
                            # A correction fired from a button press, not part
                            # of any answer — keep it out of the live card.
                            metadata={_CARD_BYPASS_METADATA_KEY: True},
                        )
                    except Exception:
                        logger.debug("[Feishu] expired-approval notice failed", exc_info=True)
        except Exception as exc:
            logger.error("Failed to resolve gateway approval from Feishu button: %s", exc)

    async def _resolve_update_prompt(
        self,
        prompt_id: Any,
        answer: str,
        user_name: str,
        *,
        open_id: str = "",
        chat_id: str = "",
    ) -> None:
        """Persist an update prompt answer for the detached update process."""
        state = self._update_prompt_state.get(prompt_id)
        if not state:
            logger.debug("[Feishu] Update prompt %s already resolved or unknown", prompt_id)
            return
        if open_id:
            sender_id = SimpleNamespace(open_id=open_id, user_id="")
            if not self._allow_group_message(sender_id, state.get("chat_id", ""), is_bot=False):
                logger.warning("[Feishu] Unauthorized update prompt click by %s for prompt %s", open_id, prompt_id)
                return
        expected_chat_id = str(state.get("chat_id", "") or "")
        if expected_chat_id and chat_id and expected_chat_id != chat_id:
            logger.warning(
                "[Feishu] Update prompt %s chat mismatch (expected=%s, got=%s)",
                prompt_id,
                expected_chat_id,
                chat_id,
            )
            return
        state = self._update_prompt_state.pop(prompt_id, None)
        if not state:
            logger.debug("[Feishu] Update prompt %s already resolved while validating callback", prompt_id)
            return
        try:
            self._write_update_prompt_response(answer)
            logger.info(
                "Feishu update prompt resolved for session %s (answer=%s, user=%s)",
                state["session_key"], answer, user_name,
            )
        except Exception as exc:
            logger.error("Failed to resolve Feishu update prompt: %s", exc)

    async def _handle_reaction_event(self, event_type: str, data: Any) -> None:
        """Fetch the reacted-to message; if it was sent by this bot, emit a synthetic text event."""
        if not self._client:
            return
        event = getattr(data, "event", None)
        message_id = str(getattr(event, "message_id", "") or "")
        if not message_id:
            return

        # Fetch the target message to verify it was sent by us and to obtain chat context.
        try:
            request = self._build_get_message_request(message_id)
            response = await self._run_blocking(self._client.im.v1.message.get, request)
            if not response or not getattr(response, "success", lambda: False)():
                return
            items = getattr(getattr(response, "data", None), "items", None) or []
            msg = items[0] if items else None
            if not msg:
                return
            # GET im/v1/messages returns sender.id=app_id for bot messages —
            # peer bots and us share sender_type="app" but differ on app_id.
            sender = getattr(msg, "sender", None)
            if str(getattr(sender, "id", "") or "") != self._app_id:
                return  # only route reactions on this bot's own messages
            chat_id = str(getattr(msg, "chat_id", "") or "")
            chat_type_raw = str(getattr(msg, "chat_type", "p2p") or "p2p")
            if not chat_id:
                return
        except Exception:
            logger.debug("[Feishu] Failed to fetch message for reaction routing", exc_info=True)
            return

        user_id_obj = getattr(event, "user_id", None)
        reaction_type_obj = getattr(event, "reaction_type", None)
        emoji_type = str(getattr(reaction_type_obj, "emoji_type", "") or "UNKNOWN")
        action = "added" if "created" in event_type else "removed"
        synthetic_text = f"reaction:{action}:{emoji_type}"

        sender_profile = await self._resolve_sender_profile(user_id_obj)
        chat_info = await self.get_chat_info(chat_id)
        source = self.build_source(
            chat_id=chat_id,
            chat_name=chat_info.get("name") or chat_id or "Feishu Chat",
            chat_type=self._resolve_source_chat_type(chat_info=chat_info, event_chat_type=chat_type_raw),
            user_id=sender_profile["user_id"],
            user_name=sender_profile["user_name"],
            thread_id=None,
            user_id_alt=sender_profile["user_id_alt"],
        )
        synthetic_event = MessageEvent(
            text=synthetic_text,
            message_type=MessageType.TEXT,
            source=source,
            raw_message=data,
            message_id=message_id,
            channel_prompt=self._resolve_channel_prompt(chat_id),
            timestamp=datetime.now(),
        )
        logger.info("[Feishu] Routing reaction %s:%s on bot message %s as synthetic event", action, emoji_type, message_id)
        await self._handle_message_with_guards(synthetic_event)

    def _is_card_action_duplicate(self, token: str) -> bool:
        """Return True if this card action token was already processed within the dedup window."""
        now = time.time()
        # Prune expired tokens lazily each call.
        expired = [t for t, ts in self._card_action_tokens.items() if now - ts > _FEISHU_CARD_ACTION_DEDUP_TTL_SECONDS]
        for t in expired:
            del self._card_action_tokens[t]
        if token in self._card_action_tokens:
            return True
        self._card_action_tokens[token] = now
        return False

    async def _handle_card_action_event(self, data: Any) -> None:
        """Route Feishu interactive card button clicks as synthetic COMMAND events."""
        event = getattr(data, "event", None)
        token = str(getattr(event, "token", "") or "")
        if token and self._is_card_action_duplicate(token):
            logger.debug("[Feishu] Dropping duplicate card action token: %s", token)
            return

        context = getattr(event, "context", None)
        chat_id = str(getattr(context, "open_chat_id", "") or "")
        operator = getattr(event, "operator", None)
        open_id = str(getattr(operator, "open_id", "") or "")
        if not chat_id or not open_id:
            logger.debug("[Feishu] Card action missing chat_id or operator open_id, dropping")
            return

        action = getattr(event, "action", None)
        action_tag = str(getattr(action, "tag", "") or "button")
        action_value = getattr(action, "value", {}) or {}

        synthetic_text = f"/card {action_tag}"
        if action_value:
            try:
                synthetic_text += f" {json.dumps(action_value, ensure_ascii=False)}"
            except Exception:
                pass

        sender_id = SimpleNamespace(open_id=open_id, user_id=None, union_id=None)
        sender_profile = await self._resolve_sender_profile(sender_id)
        chat_info = await self.get_chat_info(chat_id)
        source = self.build_source(
            chat_id=chat_id,
            chat_name=chat_info.get("name") or chat_id or "Feishu Chat",
            chat_type=self._resolve_source_chat_type(chat_info=chat_info, event_chat_type="group"),
            user_id=sender_profile["user_id"],
            user_name=sender_profile["user_name"],
            thread_id=None,
            user_id_alt=sender_profile["user_id_alt"],
        )
        synthetic_event = MessageEvent(
            text=synthetic_text,
            message_type=MessageType.COMMAND,
            source=source,
            raw_message=data,
            message_id=token or str(uuid.uuid4()),
            channel_prompt=self._resolve_channel_prompt(chat_id),
            timestamp=datetime.now(),
        )
        logger.info("[Feishu] Routing card action %r from %s in %s as synthetic command", action_tag, open_id, chat_id)
        await self._handle_message_with_guards(synthetic_event)

    # =========================================================================
    # Per-chat serialization and typing indicator
    # =========================================================================

    def _get_chat_lock(self, chat_id: str) -> asyncio.Lock:
        """Return (creating if needed) the per-chat asyncio.Lock for serial message processing.

        Bounded with LRU eviction so a long-running gateway that sees many
        distinct chats does not grow ``_chat_locks`` without limit. Locks that
        are currently held are never evicted; if every entry is locked we fall
        back to dropping the least-recently-used one.
        """
        lock = self._chat_locks.get(chat_id)
        if lock is not None:
            self._chat_locks.move_to_end(chat_id)
            return lock
        if len(self._chat_locks) >= self.CHAT_LOCK_MAX_SIZE:
            evicted = False
            for key in list(self._chat_locks):
                if not self._chat_locks[key].locked():
                    self._chat_locks.pop(key)
                    evicted = True
                    break
            if not evicted:
                self._chat_locks.pop(next(iter(self._chat_locks)))
        lock = asyncio.Lock()
        self._chat_locks[chat_id] = lock
        return lock

    async def _handle_message_with_guards(self, event: MessageEvent) -> None:
        """Dispatch a single event through the agent pipeline with per-chat serialization
        before handing the event off to the agent.

        Per-chat lock ensures messages in the same chat are processed one at a
        time (matches openclaw's createChatQueue serial queue behaviour).
        """
        chat_id = getattr(event.source, "chat_id", "") or "" if event.source else ""
        chat_lock = self._get_chat_lock(chat_id)
        async with chat_lock:
            await self.handle_message(event)

    # =========================================================================
    # Processing status reactions
    # =========================================================================

    def _reactions_enabled(self) -> bool:
        return os.getenv("FEISHU_REACTIONS", "true").strip().lower() not in {"false", "0", "no"}

    async def _add_reaction(self, message_id: str, emoji_type: str) -> Optional[str]:
        """Return the reaction_id on success, else None. The id is needed later for deletion."""
        if not self._client or not message_id or not emoji_type:
            return None
        try:
            from lark_oapi.api.im.v1 import (
                CreateMessageReactionRequest,
                CreateMessageReactionRequestBody,
            )
            body = (
                CreateMessageReactionRequestBody.builder()
                .reaction_type({"emoji_type": emoji_type})
                .build()
            )
            request = (
                CreateMessageReactionRequest.builder()
                .message_id(message_id)
                .request_body(body)
                .build()
            )
            response = await self._run_blocking(self._client.im.v1.message_reaction.create, request)
            if response and getattr(response, "success", lambda: False)():
                data = getattr(response, "data", None)
                return getattr(data, "reaction_id", None)
            logger.debug(
                "[Feishu] Add reaction %s on %s rejected: code=%s msg=%s",
                emoji_type,
                message_id,
                getattr(response, "code", None),
                getattr(response, "msg", None),
            )
        except Exception:
            logger.warning(
                "[Feishu] Add reaction %s on %s raised",
                emoji_type,
                message_id,
                exc_info=True,
            )
        return None

    async def _remove_reaction(self, message_id: str, reaction_id: str) -> bool:
        if not self._client or not message_id or not reaction_id:
            return False
        try:
            from lark_oapi.api.im.v1 import DeleteMessageReactionRequest
            request = (
                DeleteMessageReactionRequest.builder()
                .message_id(message_id)
                .reaction_id(reaction_id)
                .build()
            )
            response = await self._run_blocking(self._client.im.v1.message_reaction.delete, request)
            if response and getattr(response, "success", lambda: False)():
                return True
            logger.debug(
                "[Feishu] Remove reaction %s on %s rejected: code=%s msg=%s",
                reaction_id,
                message_id,
                getattr(response, "code", None),
                getattr(response, "msg", None),
            )
        except Exception:
            logger.warning(
                "[Feishu] Remove reaction %s on %s raised",
                reaction_id,
                message_id,
                exc_info=True,
            )
        return False

    def _remember_processing_reaction(self, message_id: str, reaction_id: str) -> None:
        cache = self._pending_processing_reactions
        cache[message_id] = reaction_id
        cache.move_to_end(message_id)
        while len(cache) > _FEISHU_PROCESSING_REACTION_CACHE_SIZE:
            cache.popitem(last=False)

    def _pop_processing_reaction(self, message_id: str) -> Optional[str]:
        return self._pending_processing_reactions.pop(message_id, None)

    async def on_processing_start(self, event: MessageEvent) -> None:
        self._card_begin_turn(event)
        if not self._reactions_enabled():
            return
        message_id = event.message_id
        if not message_id or message_id in self._pending_processing_reactions:
            return
        reaction_id = await self._add_reaction(message_id, _FEISHU_REACTION_IN_PROGRESS)
        if reaction_id:
            self._remember_processing_reaction(message_id, reaction_id)

    def _card_begin_turn(self, event: MessageEvent) -> None:
        """Open the card window for this turn.

        Only output produced inside a turn becomes a card; a slash-command
        reply or a cron push would otherwise turn into a card whose
        execution panel is permanently empty.
        """
        manager = getattr(self, "_card_manager", None)
        if manager is None:
            return
        source = getattr(event, "source", None)
        thread_id = getattr(source, "thread_id", None)
        try:
            manager.begin_turn(
                chat_id=str(getattr(source, "chat_id", "") or ""),
                thread_id=thread_id,
                reply_to=event.message_id if thread_id else None,
                metadata={"thread_id": thread_id} if thread_id else None,
            )
        except Exception:
            logger.debug("[Feishu] card begin_turn failed", exc_info=True)

    async def _card_finish_turn(
        self, event: MessageEvent, outcome: ProcessingOutcome
    ) -> None:
        """Seal the turn's card: collapse the trace, stop the streaming animation."""
        manager = getattr(self, "_card_manager", None)
        if manager is None:
            return
        source = getattr(event, "source", None)
        try:
            await manager.finish_turn(
                chat_id=str(getattr(source, "chat_id", "") or ""),
                thread_id=getattr(source, "thread_id", None),
                failed=outcome is ProcessingOutcome.FAILURE,
            )
        except Exception:
            logger.debug("[Feishu] card finish_turn failed", exc_info=True)
        finally:
            getattr(self, "_delegate_card_blocks", {}).clear()
            getattr(self, "_delegate_card_locks", {}).clear()
            getattr(self, "_delegate_card_streamed", set()).clear()
            # The attachment ledger is per-turn: "send me that file again"
            # in a later turn has to upload it again.
            getattr(self, "_card_media_dispatched", {}).clear()

    async def on_processing_complete(
        self, event: MessageEvent, outcome: ProcessingOutcome
    ) -> None:
        await self._card_finish_turn(event, outcome)
        if not self._reactions_enabled():
            return
        message_id = event.message_id
        if not message_id:
            return

        start_reaction_id = self._pending_processing_reactions.get(message_id)
        if start_reaction_id:
            if not await self._remove_reaction(message_id, start_reaction_id):
                # Don't stack a second badge on top of a Typing we couldn't
                # remove — UI would read as both "working" and "done/failed"
                # simultaneously. Keep the handle so LRU eventually evicts it.
                return
            self._pop_processing_reaction(message_id)

        if outcome is ProcessingOutcome.FAILURE:
            await self._add_reaction(message_id, _FEISHU_REACTION_FAILURE)

    # =========================================================================
    # Webhook server and security
    # =========================================================================

    def _record_webhook_anomaly(self, remote_ip: str, status: str) -> None:
        """Increment the anomaly counter for remote_ip and emit a WARNING every threshold hits.

        Mirrors openclaw's createWebhookAnomalyTracker: TTL 6 hours, log every 25 consecutive
        error responses from the same IP.
        """
        now = time.time()
        entry = self._webhook_anomaly_counts.get(remote_ip)
        if entry is not None:
            count, _last_status, first_seen = entry
            if now - first_seen < _FEISHU_WEBHOOK_ANOMALY_TTL_SECONDS:
                count += 1
                if count % _FEISHU_WEBHOOK_ANOMALY_THRESHOLD == 0:
                    logger.warning(
                        "[Feishu] Webhook anomaly: %d consecutive error responses (%s) from %s "
                        "over the last %.0fs",
                        count,
                        status,
                        remote_ip,
                        now - first_seen,
                    )
                self._webhook_anomaly_counts[remote_ip] = (count, status, first_seen)
                return
        # Either first occurrence or TTL expired — start fresh.
        self._webhook_anomaly_counts[remote_ip] = (1, status, now)

    def _clear_webhook_anomaly(self, remote_ip: str) -> None:
        """Reset the anomaly counter for remote_ip after a successful request."""
        self._webhook_anomaly_counts.pop(remote_ip, None)

    # =========================================================================
    # Inbound processing pipeline
    # =========================================================================

    def _resolve_channel_prompt(self, chat_id: str, parent_id: str | None = None) -> str | None:
        """Resolve a Feishu per-channel system prompt.

        Mirrors the Discord/Slack behaviour so ``channel_prompts: {<chat_id>:
        "<prompt>"}`` in ``PlatformConfig.extra`` is honoured for Feishu chats
        instead of being silently ignored.
        """
        from gateway.platforms.base import resolve_channel_prompt
        _config = getattr(self, "config", None)
        _extra = getattr(_config, "extra", None) or {}
        return resolve_channel_prompt(_extra, chat_id, parent_id)

    async def _process_inbound_message(
        self,
        *,
        data: Any,
        message: Any,
        sender_id: Any,
        chat_type: str,
        message_id: str,
        is_bot: bool = False,
    ) -> None:
        text, inbound_type, media_urls, media_types, mentions = await self._extract_message_content(message)

        if inbound_type == MessageType.TEXT:
            text = _strip_edge_self_mentions(text, mentions)
            if text.startswith("/"):
                inbound_type = MessageType.COMMAND

        # Guard runs post-strip so a pure "@Bot" message (stripped to "") is dropped.
        if inbound_type == MessageType.TEXT and not text and not media_urls:
            logger.debug("[Feishu] Ignoring empty text message id=%s", message_id)
            return

        if inbound_type != MessageType.COMMAND:
            hint = _build_mention_hint(mentions)
            if hint:
                text = f"{hint}\n\n{text}" if text else hint

        actual_thread_id = getattr(message, "thread_id", None) or None
        root_message_id = getattr(message, "root_id", None) or None
        thread_id = actual_thread_id or root_message_id or None
        reply_to_message_id = (
            getattr(message, "parent_id", None)
            or getattr(message, "upper_message_id", None)
            or getattr(message, "root_id", None)
            or None
        )
        reply_to_text = await self._fetch_message_text(reply_to_message_id) if reply_to_message_id else None

        sender_primary = (
            getattr(sender_id, "open_id", None)
            or getattr(sender_id, "user_id", None)
            or getattr(sender_id, "union_id", None)
            or "<unknown>"
        )
        logger.info(
            "[Feishu] Inbound %s message received: id=%s type=%s chat_id=%s sender=%s:%s text=%r media=%d",
            "dm" if chat_type == "p2p" else "group",
            message_id,
            inbound_type.value,
            getattr(message, "chat_id", "") or "",
            "bot" if is_bot else "user",
            sender_primary,
            text[:120],
            len(media_urls),
        )

        chat_id = getattr(message, "chat_id", "") or ""
        chat_info = await self.get_chat_info(chat_id)
        sender_profile = await self._resolve_sender_profile(sender_id, is_bot=is_bot)
        source_chat_type = self._resolve_source_chat_type(
            chat_info=chat_info,
            event_chat_type=chat_type,
        )
        source = self.build_source(
            chat_id=chat_id,
            chat_name=chat_info.get("name") or chat_id or "Feishu Chat",
            chat_type=source_chat_type,
            user_id=sender_profile["user_id"],
            user_name=sender_profile["user_name"],
            thread_id=thread_id,
            user_id_alt=sender_profile["user_id_alt"],
            is_bot=is_bot,
            message_id=message_id,
        )
        if (
            self._reply_thread_enabled()
            and source_chat_type in {"dm", "group"}
            and not actual_thread_id
            and not root_message_id
            and message_id
        ):
            # Feishu creates the real ``omt_*`` thread only after the first
            # reply. Key the initiating turn on its stable ``om_*`` root now;
            # the send path treats this value as a reply anchor, not a receive
            # id, and therefore creates the topic with reply_in_thread=true.
            thread_id = str(message_id)
            source.thread_id = thread_id
            self._mark_auto_thread_pending(thread_id)
        elif actual_thread_id and root_message_id:
            # For a later message in an auto-created topic, reuse the root-keyed
            # session only when that root already exists. Human-created topics
            # keep their historical real-thread (omt_*) session identity.
            real_thread_id = source.thread_id
            source.thread_id = str(root_message_id)
            if self._session_exists_for_source(source):
                thread_id = source.thread_id
                self._mark_auto_thread_established(str(root_message_id))
            else:
                source.thread_id = real_thread_id
                thread_id = real_thread_id
        # Foreground A2A loops own their route before normal dispatch, text
        # batching, or per-chat serialization can turn a follow-up into a new
        # main-agent turn.  `text` has already had a leading bot mention
        # removed, so exact loop-exit commands remain recognisable.
        delegate_command_text = str(text or "").strip()
        delegate_routed_text = (
            delegate_command_text
            if delegate_command_text in {"/main", "/exit"}
            else text
        )
        if await self._maybe_route_delegate_interaction_message(
            text=delegate_routed_text,
            chat_id=chat_id,
            thread_id=thread_id,
            user_id=getattr(source, "user_id", sender_profile["user_id"]),
            chat_type=getattr(
                source,
                "chat_type",
                self._resolve_source_chat_type(chat_info=chat_info, event_chat_type=chat_type),
            ),
        ):
            return
        if await self._maybe_route_delegate_foreground_message(
            text=delegate_routed_text,
            chat_id=chat_id,
            thread_id=thread_id,
            # ``build_source`` normally returns SessionSource, but a few
            # embedders and lightweight test adapters provide only the
            # fields needed for MessageEvent dispatch.  Route against the
            # already-resolved identities when those optional attributes are
            # absent rather than turning an ordinary inbound message into an
            # adapter error.
            user_id=getattr(source, "user_id", sender_profile["user_id"]),
            chat_type=getattr(
                source,
                "chat_type",
                self._resolve_source_chat_type(chat_info=chat_info, event_chat_type=chat_type),
            ),
        ):
            return
        normalized = MessageEvent(
            text=text,
            message_type=inbound_type,
            source=source,
            raw_message=data,
            message_id=message_id,
            media_urls=media_urls,
            media_types=media_types,
            reply_to_message_id=reply_to_message_id,
            reply_to_text=reply_to_text,
            channel_prompt=self._resolve_channel_prompt(chat_id, thread_id or None),
            timestamp=datetime.now(),
        )
        await self._dispatch_inbound_event(normalized)

    async def _dispatch_inbound_event(self, event: MessageEvent) -> None:
        """Apply Feishu-specific burst protection before entering the base adapter."""
        if event.message_type == MessageType.TEXT and not event.is_command():
            await self._enqueue_text_event(event)
            return
        if self._should_batch_media_event(event):
            await self._enqueue_media_event(event)
            return
        await self._handle_message_with_guards(event)

    # =========================================================================
    # Media batching
    # =========================================================================

    def _should_batch_media_event(self, event: MessageEvent) -> bool:
        return bool(
            event.media_urls
            and event.message_type in {MessageType.PHOTO, MessageType.VIDEO, MessageType.DOCUMENT, MessageType.AUDIO}
        )

    def _media_batch_key(self, event: MessageEvent) -> str:
        from gateway.session import build_session_key

        session_key = build_session_key(
            event.source,
            group_sessions_per_user=self.config.extra.get("group_sessions_per_user", True),
            thread_sessions_per_user=self.config.extra.get("thread_sessions_per_user", False),
        )
        return f"{session_key}:media:{event.message_type.value}"

    @staticmethod
    def _media_batch_is_compatible(existing: MessageEvent, incoming: MessageEvent) -> bool:
        return (
            existing.message_type == incoming.message_type
            and existing.reply_to_message_id == incoming.reply_to_message_id
            and existing.reply_to_text == incoming.reply_to_text
            and existing.source.thread_id == incoming.source.thread_id
        )

    async def _enqueue_media_event(self, event: MessageEvent) -> None:
        key = self._media_batch_key(event)
        existing = self._pending_media_batches.get(key)
        if existing is None:
            self._pending_media_batches[key] = event
            self._schedule_media_batch_flush(key)
            return
        if not self._media_batch_is_compatible(existing, event):
            await self._flush_media_batch_now(key)
            self._pending_media_batches[key] = event
            self._schedule_media_batch_flush(key)
            return
        existing.media_urls.extend(event.media_urls)
        existing.media_types.extend(event.media_types)
        if event.text:
            existing.text = self._merge_caption(existing.text, event.text)
        existing.timestamp = event.timestamp
        if event.message_id:
            existing.message_id = event.message_id
        self._schedule_media_batch_flush(key)

    def _schedule_media_batch_flush(self, key: str) -> None:
        self._reschedule_batch_task(
            self._pending_media_batch_tasks,
            key,
            self._flush_media_batch,
        )

    async def _flush_media_batch(self, key: str) -> None:
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(self._media_batch_delay_seconds)
            await self._flush_media_batch_now(key)
        finally:
            if self._pending_media_batch_tasks.get(key) is current_task:
                self._pending_media_batch_tasks.pop(key, None)

    async def _flush_media_batch_now(self, key: str) -> None:
        event = self._pending_media_batches.pop(key, None)
        if not event:
            return
        logger.info(
            "[Feishu] Flushing media batch %s with %d attachment(s)",
            key,
            len(event.media_urls),
        )
        await self._handle_message_with_guards(event)

    async def _download_remote_image(self, image_url: str) -> str:
        ext = self._guess_remote_extension(image_url, default=".jpg")
        return await cache_image_from_url(image_url, ext=ext)

    async def _download_remote_document(
        self,
        file_url: str,
        *,
        default_ext: str,
        preferred_name: str,
    ) -> tuple[str, str]:
        from gateway.platforms.base import _ssrf_redirect_guard
        from tools.url_safety import create_ssrf_safe_async_client, is_safe_url

        if not is_safe_url(file_url):
            raise ValueError(f"Blocked unsafe URL (SSRF protection): {file_url[:80]}")

        async with create_ssrf_safe_async_client(
            timeout=30.0,
            follow_redirects=True,
            event_hooks={"response": [_ssrf_redirect_guard]},
        ) as client:
            response = await client.get(
                file_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; HermesAgent/1.0)",
                    "Accept": "*/*",
                },
            )
            response.raise_for_status()
            # Snapshot Content-Type and body while the client context is
            # still active so pooled connections fully release on exit.
            # See #18451.
            content_type_hdr = str(response.headers.get("Content-Type", ""))
            body = response.content
        filename = self._derive_remote_filename(
            file_url,
            content_type=content_type_hdr,
            default_name=preferred_name,
            default_ext=default_ext,
        )
        cached_path = cache_document_from_bytes(body, filename)
        return cached_path, filename

    @staticmethod
    def _guess_remote_extension(url: str, *, default: str) -> str:
        ext = Path((url or "").split("?", 1)[0]).suffix.lower()
        return ext if ext in (_IMAGE_EXTENSIONS | _AUDIO_EXTENSIONS | _VIDEO_EXTENSIONS | set(SUPPORTED_DOCUMENT_TYPES)) else default

    @staticmethod
    def _derive_remote_filename(file_url: str, *, content_type: str, default_name: str, default_ext: str) -> str:
        candidate = Path((file_url or "").split("?", 1)[0]).name or default_name
        ext = Path(candidate).suffix.lower()
        if not ext:
            guessed = mimetypes.guess_extension((content_type or "").split(";", 1)[0].strip().lower() or "") or default_ext
            candidate = f"{candidate}{guessed}"
        return candidate

    @staticmethod
    def _namespace_from_mapping(value: Any) -> Any:
        if isinstance(value, dict):
            return SimpleNamespace(**{key: FeishuAdapter._namespace_from_mapping(item) for key, item in value.items()})
        if isinstance(value, list):
            return [FeishuAdapter._namespace_from_mapping(item) for item in value]
        return value

    async def _handle_webhook_request(self, request: Any) -> Any:
        remote_ip = (getattr(request, "remote", None) or "unknown")

        # Rate limiting — composite key: app_id:path:remote_ip (matches openclaw key structure).
        rate_key = f"{self._app_id}:{self._webhook_path}:{remote_ip}"
        if not self._check_webhook_rate_limit(rate_key):
            logger.warning("[Feishu] Webhook rate limit exceeded for %s", remote_ip)
            self._record_webhook_anomaly(remote_ip, "429")
            return web.Response(status=429, text="Too Many Requests")

        # Content-Type guard — Feishu always sends application/json.
        headers = getattr(request, "headers", {}) or {}
        content_type = str(headers.get("Content-Type", "") or "").split(";")[0].strip().lower()
        if content_type and content_type != "application/json":
            logger.warning("[Feishu] Webhook rejected: unexpected Content-Type %r from %s", content_type, remote_ip)
            self._record_webhook_anomaly(remote_ip, "415")
            return web.Response(status=415, text="Unsupported Media Type")

        # Body size guard — reject early via Content-Length when present.
        content_length = getattr(request, "content_length", None)
        if content_length is not None and content_length > _FEISHU_WEBHOOK_MAX_BODY_BYTES:
            logger.warning("[Feishu] Webhook body too large (%d bytes) from %s", content_length, remote_ip)
            self._record_webhook_anomaly(remote_ip, "413")
            return web.Response(status=413, text="Request body too large")

        try:
            body_bytes: bytes = await asyncio.wait_for(
                _read_limited_feishu_webhook_body(
                    request,
                    _FEISHU_WEBHOOK_MAX_BODY_BYTES,
                ),
                timeout=_FEISHU_WEBHOOK_BODY_TIMEOUT_SECONDS,
            )
        except ValueError:
            logger.warning("[Feishu] Webhook body exceeds limit from %s", remote_ip)
            self._record_webhook_anomaly(remote_ip, "413")
            return web.Response(status=413, text="Request body too large")
        except asyncio.TimeoutError:
            logger.warning("[Feishu] Webhook body read timed out after %ds from %s", _FEISHU_WEBHOOK_BODY_TIMEOUT_SECONDS, remote_ip)
            self._record_webhook_anomaly(remote_ip, "408")
            return web.Response(status=408, text="Request Timeout")
        except Exception:
            self._record_webhook_anomaly(remote_ip, "400")
            return web.json_response({"code": 400, "msg": "failed to read body"}, status=400)

        try:
            payload = json.loads(body_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._record_webhook_anomaly(remote_ip, "400")
            return web.json_response({"code": 400, "msg": "invalid json"}, status=400)

        # Verification token check — second layer of defence beyond signature (matches openclaw).
        if self._verification_token:
            header = payload.get("header") or {}
            incoming_token = str(header.get("token") or payload.get("token") or "")
            # Compare as bytes: compare_digest raises TypeError on a str with
            # non-ASCII characters, and the token comes from the request body.
            if not incoming_token or not hmac.compare_digest(
                incoming_token.encode(), self._verification_token.encode()
            ):
                logger.warning("[Feishu] Webhook rejected: invalid verification token from %s", remote_ip)
                self._record_webhook_anomaly(remote_ip, "401-token")
                return web.Response(status=401, text="Invalid verification token")

        # URL verification challenge — Feishu includes the verification token in
        # challenge requests. Validate the token (above) before reflecting the
        # challenge so an unauthenticated remote request cannot prove endpoint
        # control by getting attacker-supplied challenge data echoed back.
        if payload.get("type") == "url_verification":
            return web.json_response({"challenge": payload.get("challenge", "")})

        # Timing-safe signature verification (only enforced when encrypt_key is set).
        if self._encrypt_key and not self._is_webhook_signature_valid(request.headers, body_bytes):
            logger.warning("[Feishu] Webhook rejected: invalid signature from %s", remote_ip)
            self._record_webhook_anomaly(remote_ip, "401-sig")
            return web.Response(status=401, text="Invalid signature")

        if payload.get("encrypt"):
            logger.error("[Feishu] Encrypted webhook payloads are not supported by Hermes webhook mode")
            self._record_webhook_anomaly(remote_ip, "400-encrypted")
            return web.json_response({"code": 400, "msg": "encrypted webhook payloads are not supported"}, status=400)

        self._clear_webhook_anomaly(remote_ip)

        event_type = str((payload.get("header") or {}).get("event_type") or "")
        data = self._namespace_from_mapping(payload)
        if event_type == "im.message.receive_v1":
            self._on_message_event(data)
        elif event_type == "im.message.message_read_v1":
            self._on_message_read_event(data)
        elif event_type == "im.chat.member.bot.added_v1":
            self._on_bot_added_to_chat(data)
        elif event_type == "im.chat.member.bot.deleted_v1":
            self._on_bot_removed_from_chat(data)
        elif event_type in {"im.message.reaction.created_v1", "im.message.reaction.deleted_v1"}:
            self._on_reaction_event(event_type, data)
        elif event_type == "card.action.trigger":
            self._on_card_action_trigger(data)
        elif event_type == "drive.notice.comment_add_v1":
            self._on_drive_comment_event(data)
        elif event_type == "vc.bot.meeting_invited_v1":
            self._on_meeting_invited_event(data)
        else:
            logger.debug("[Feishu] Ignoring webhook event type: %s", event_type or "unknown")
        return web.json_response({"code": 0, "msg": "ok"})

    def _is_webhook_signature_valid(self, headers: Any, body_bytes: bytes) -> bool:
        """Verify Feishu webhook signature using timing-safe comparison.

        Feishu signature algorithm:
            SHA256(timestamp + nonce + encrypt_key + body_string)
        Headers checked: x-lark-request-timestamp, x-lark-request-nonce, x-lark-signature.
        """
        timestamp = str(headers.get("x-lark-request-timestamp", "") or "")
        nonce = str(headers.get("x-lark-request-nonce", "") or "")
        signature = str(headers.get("x-lark-signature", "") or "")
        if not timestamp or not nonce or not signature:
            return False
        try:
            body_str = body_bytes.decode("utf-8", errors="replace")
            content = f"{timestamp}{nonce}{self._encrypt_key}{body_str}"
            computed = hashlib.sha256(content.encode("utf-8")).hexdigest()
            # Compare as bytes: compare_digest raises TypeError on a str with
            # non-ASCII characters, and the signature is a raw request header.
            return hmac.compare_digest(computed.encode(), signature.encode())
        except Exception:
            logger.debug("[Feishu] Signature verification raised an exception", exc_info=True)
            return False

    def _check_webhook_rate_limit(self, rate_key: str) -> bool:
        """Return False when the composite rate_key has exceeded _FEISHU_WEBHOOK_RATE_LIMIT_MAX.

        The rate_key is composed as "{app_id}:{path}:{remote_ip}" — matching openclaw's key
        structure so the limit is scoped to a specific (account, endpoint, IP) triple rather
        than a bare IP, which causes fewer false-positive denials in multi-tenant setups.

        The tracking dict is capped at _FEISHU_WEBHOOK_RATE_MAX_KEYS entries to prevent unbounded
        memory growth. Stale (expired) entries are pruned when the cap is reached.
        """
        now = time.time()
        # Fast path: existing entry within the current window.
        entry = self._webhook_rate_counts.get(rate_key)
        if entry is not None:
            count, window_start = entry
            if now - window_start < _FEISHU_WEBHOOK_RATE_WINDOW_SECONDS:
                if count >= _FEISHU_WEBHOOK_RATE_LIMIT_MAX:
                    return False
                self._webhook_rate_counts[rate_key] = (count + 1, window_start)
                return True
        # New window for an existing key, or a brand-new key — prune stale entries first.
        if len(self._webhook_rate_counts) >= _FEISHU_WEBHOOK_RATE_MAX_KEYS:
            stale_keys = [
                k for k, (_, ws) in self._webhook_rate_counts.items()
                if now - ws >= _FEISHU_WEBHOOK_RATE_WINDOW_SECONDS
            ]
            for k in stale_keys:
                del self._webhook_rate_counts[k]
            # If still at capacity after pruning, deny untracked keys (fail closed).
            # The table only fills with this many distinct (account, endpoint, IP)
            # triples under abuse; allowing untracked requests through at capacity
            # would let an attacker who flooded the table bypass the limiter entirely.
            if rate_key not in self._webhook_rate_counts and len(self._webhook_rate_counts) >= _FEISHU_WEBHOOK_RATE_MAX_KEYS:
                logger.warning(
                    "[Feishu] Webhook rate-limit table at capacity (%d keys) — denying untracked key",
                    _FEISHU_WEBHOOK_RATE_MAX_KEYS,
                )
                return False
        self._webhook_rate_counts[rate_key] = (1, now)
        return True

    # =========================================================================
    # Text batching
    # =========================================================================

    def _text_batch_key(self, event: MessageEvent) -> str:
        """Return the session-scoped key used for Feishu text aggregation."""
        from gateway.session import build_session_key

        return build_session_key(
            event.source,
            group_sessions_per_user=self.config.extra.get("group_sessions_per_user", True),
            thread_sessions_per_user=self.config.extra.get("thread_sessions_per_user", False),
            profile=event.source.profile,
        )

    @staticmethod
    def _text_batch_is_compatible(existing: MessageEvent, incoming: MessageEvent) -> bool:
        """Only merge text events when reply/thread context is identical."""
        return (
            existing.reply_to_message_id == incoming.reply_to_message_id
            and existing.reply_to_text == incoming.reply_to_text
            and existing.source.thread_id == incoming.source.thread_id
        )

    async def _enqueue_text_event(self, event: MessageEvent) -> None:
        """Debounce rapid Feishu text bursts into a single MessageEvent."""
        key = self._text_batch_key(event)
        chunk_len = len(event.text or "")
        existing = self._pending_text_batches.get(key)
        if existing is None:
            event._last_chunk_len = chunk_len  # type: ignore[attr-defined]
            self._pending_text_batches[key] = event
            self._pending_text_batch_counts[key] = 1
            self._schedule_text_batch_flush(key)
            return

        if not self._text_batch_is_compatible(existing, event):
            await self._flush_text_batch_now(key)
            self._pending_text_batches[key] = event
            self._pending_text_batch_counts[key] = 1
            self._schedule_text_batch_flush(key)
            return

        existing_count = self._pending_text_batch_counts.get(key, 1)
        next_count = existing_count + 1
        appended_text = event.text or ""
        next_text = f"{existing.text}\n{appended_text}" if existing.text and appended_text else (existing.text or appended_text)
        if next_count > self._text_batch_max_messages or len(next_text) > self._text_batch_max_chars:
            await self._flush_text_batch_now(key)
            self._pending_text_batches[key] = event
            self._pending_text_batch_counts[key] = 1
            self._schedule_text_batch_flush(key)
            return

        existing.text = next_text
        existing._last_chunk_len = chunk_len  # type: ignore[attr-defined]
        existing.timestamp = event.timestamp
        if event.message_id:
            existing.message_id = event.message_id
        self._pending_text_batch_counts[key] = next_count
        self._schedule_text_batch_flush(key)

    def _schedule_text_batch_flush(self, key: str) -> None:
        """Reset the debounce timer for a pending Feishu text batch."""
        self._reschedule_batch_task(
            self._pending_text_batch_tasks,
            key,
            self._flush_text_batch,
        )

    @staticmethod
    def _reschedule_batch_task(
        task_map: Dict[str, asyncio.Task],
        key: str,
        flush_fn: Any,
    ) -> None:
        prior_task = task_map.get(key)
        if prior_task and not prior_task.done():
            prior_task.cancel()
        task_map[key] = asyncio.create_task(flush_fn(key))

    async def _flush_text_batch(self, key: str) -> None:
        """Flush a pending text batch after the quiet period.

        Uses a longer delay when the latest chunk is near Feishu's ~4096-char
        split point, since a continuation chunk is almost certain.
        """
        current_task = asyncio.current_task()
        try:
            # Adaptive delay: if the latest chunk is near the split threshold,
            # a continuation is almost certain — wait longer.
            pending = self._pending_text_batches.get(key)
            last_len = getattr(pending, "_last_chunk_len", 0) if pending else 0
            if last_len >= self._SPLIT_THRESHOLD:
                delay = self._text_batch_split_delay_seconds
            else:
                delay = self._text_batch_delay_seconds
            await asyncio.sleep(delay)
            await self._flush_text_batch_now(key)
        finally:
            if self._pending_text_batch_tasks.get(key) is current_task:
                self._pending_text_batch_tasks.pop(key, None)

    async def _flush_text_batch_now(self, key: str) -> None:
        """Dispatch the current text batch immediately."""
        event = self._pending_text_batches.pop(key, None)
        self._pending_text_batch_counts.pop(key, None)
        if not event:
            return
        logger.info(
            "[Feishu] Flushing text batch %s (%d chars)",
            key,
            len(event.text or ""),
        )
        await self._handle_message_with_guards(event)

    # =========================================================================
    # Message content extraction and resource download
    # =========================================================================

    async def _extract_message_content(
        self, message: Any
    ) -> tuple[str, MessageType, List[str], List[str], List[FeishuMentionRef]]:
        raw_content = getattr(message, "content", "") or ""
        raw_type = getattr(message, "message_type", "") or ""
        message_id = str(getattr(message, "message_id", "") or "")
        logger.info("[Feishu] Received raw message type=%s message_id=%s", raw_type, message_id)

        normalized = normalize_feishu_message(
            message_type=raw_type,
            raw_content=raw_content,
            mentions=getattr(message, "mentions", None),
            bot=self._bot_identity(),
        )
        media_urls, media_types = await self._download_feishu_message_resources(
            message_id=message_id,
            normalized=normalized,
        )
        inbound_type = self._resolve_normalized_message_type(normalized, media_types)
        text = normalized.text_content

        if (
            inbound_type in {MessageType.DOCUMENT, MessageType.AUDIO, MessageType.VIDEO, MessageType.PHOTO}
            and len(media_urls) == 1
            and normalized.preferred_message_type in {"document", "audio"}
        ):
            injected = await self._maybe_extract_text_document(media_urls[0], media_types[0])
            if injected:
                text = injected

        return text, inbound_type, media_urls, media_types, list(normalized.mentions)

    async def _download_feishu_message_resources(
        self,
        *,
        message_id: str,
        normalized: FeishuNormalizedMessage,
    ) -> tuple[List[str], List[str]]:
        media_urls: List[str] = []
        media_types: List[str] = []

        for image_key in normalized.image_keys:
            cached_path, media_type = await self._download_feishu_image(
                message_id=message_id,
                image_key=image_key,
            )
            if cached_path:
                media_urls.append(cached_path)
                media_types.append(media_type)

        for media_ref in normalized.media_refs:
            cached_path, media_type = await self._download_feishu_message_resource(
                message_id=message_id,
                file_key=media_ref.file_key,
                resource_type=media_ref.resource_type,
                fallback_filename=media_ref.file_name,
            )
            if cached_path:
                media_urls.append(cached_path)
                media_types.append(media_type)

        return media_urls, media_types

    @staticmethod
    def _resolve_media_message_type(media_type: str, *, default: MessageType) -> MessageType:
        normalized = (media_type or "").lower()
        if normalized.startswith("image/"):
            return MessageType.PHOTO
        if normalized.startswith("audio/"):
            return MessageType.AUDIO
        if normalized.startswith("video/"):
            return MessageType.VIDEO
        return default

    def _resolve_normalized_message_type(
        self,
        normalized: FeishuNormalizedMessage,
        media_types: List[str],
    ) -> MessageType:
        preferred = normalized.preferred_message_type
        if preferred == "photo":
            return self._resolve_media_message_type(media_types[0] if media_types else "", default=MessageType.PHOTO)
        if preferred == "audio":
            # Lark's native "audio" msg_type is an in-app voice recording, not
            # an uploaded audio file (those arrive as "file"/"media" and are
            # normalized to "document"). Classify it as VOICE so the gateway
            # auto-transcribes it (Opus → STT) the same way
            # Discord/DingTalk/Telegram/etc. do — otherwise a Feishu voice note
            # reaches the agent as an untranscribable AUDIO attachment and is
            # silently ignored. Follow-up to #28993, which added native
            # voice-note transcription for Discord + DingTalk.
            return MessageType.VOICE
        if preferred == "document":
            return self._resolve_media_message_type(media_types[0] if media_types else "", default=MessageType.DOCUMENT)
        return MessageType.TEXT

    async def _maybe_extract_text_document(self, cached_path: str, media_type: str) -> str:
        if not cached_path or not media_type.startswith("text/"):
            return ""
        try:
            if os.path.getsize(cached_path) > _MAX_TEXT_INJECT_BYTES:
                return ""
            ext = Path(cached_path).suffix.lower()
            if ext not in {".txt", ".md"} and media_type not in {"text/plain", "text/markdown"}:
                return ""
            content = Path(cached_path).read_text(encoding="utf-8")
            display_name = self._display_name_from_cached_path(cached_path)
            return f"[Content of {display_name}]:\n{content}"
        except (OSError, UnicodeDecodeError):
            logger.warning("[Feishu] Failed to inject text document content from %s", cached_path, exc_info=True)
            return ""

    async def _download_feishu_image(self, *, message_id: str, image_key: str) -> tuple[str, str]:
        if not self._client or not message_id:
            return "", ""
        try:
            request = self._build_message_resource_request(
                message_id=message_id,
                file_key=image_key,
                resource_type="image",
            )
            response = await self._run_blocking(self._client.im.v1.message_resource.get, request)
            if not response or not response.success():
                logger.warning(
                    "[Feishu] Failed to download image %s: %s %s",
                    image_key,
                    getattr(response, "code", "unknown"),
                    getattr(response, "msg", "request failed"),
                )
                return "", ""
            raw_bytes = self._read_binary_response(response)
            if not raw_bytes:
                return "", ""
            content_type = self._get_response_header(response, "Content-Type")
            filename = getattr(response, "file_name", None) or f"{image_key}.jpg"
            ext = self._guess_extension(filename, content_type, ".jpg", allowed=_IMAGE_EXTENSIONS)
            cached_path = cache_image_from_bytes(raw_bytes, ext=ext)
            media_type = self._normalize_media_type(content_type, default=self._default_image_media_type(ext))
            return cached_path, media_type
        except Exception:
            logger.warning("[Feishu] Failed to cache image resource %s", image_key, exc_info=True)
            return "", ""

    async def _download_feishu_message_resource(
        self,
        *,
        message_id: str,
        file_key: str,
        resource_type: str,
        fallback_filename: str,
    ) -> tuple[str, str]:
        if not self._client or not message_id:
            return "", ""

        request_types = [resource_type]
        if resource_type in {"audio", "media"}:
            request_types.append("file")

        for request_type in request_types:
            try:
                request = self._build_message_resource_request(
                    message_id=message_id,
                    file_key=file_key,
                    resource_type=request_type,
                )
                response = await self._run_blocking(self._client.im.v1.message_resource.get, request)
                if not response or not response.success():
                    logger.debug(
                        "[Feishu] Resource download failed for %s/%s via type=%s: %s %s",
                        message_id,
                        file_key,
                        request_type,
                        getattr(response, "code", "unknown"),
                        getattr(response, "msg", "request failed"),
                    )
                    continue

                raw_bytes = self._read_binary_response(response)
                if not raw_bytes:
                    continue
                content_type = self._get_response_header(response, "Content-Type")
                response_filename = getattr(response, "file_name", None) or ""
                filename = response_filename or fallback_filename or f"{request_type}_{file_key}"
                media_type = self._normalize_media_type(
                    content_type,
                    default=self._guess_media_type_from_filename(filename),
                )

                if media_type.startswith("image/"):
                    ext = self._guess_extension(filename, content_type, ".jpg", allowed=_IMAGE_EXTENSIONS)
                    cached_path = cache_image_from_bytes(raw_bytes, ext=ext)
                    logger.info("[Feishu] Cached message image resource at %s", cached_path)
                    return cached_path, media_type or self._default_image_media_type(ext)

                if request_type == "audio" or media_type.startswith("audio/"):
                    ext = self._guess_extension(filename, content_type, ".ogg", allowed=_AUDIO_EXTENSIONS)
                    cached_path = cache_audio_from_bytes(raw_bytes, ext=ext)
                    logger.info("[Feishu] Cached message audio resource at %s", cached_path)
                    return cached_path, (media_type or f"audio/{ext.lstrip('.') or 'ogg'}")

                if media_type.startswith("video/"):
                    if not Path(filename).suffix:
                        filename = f"{filename}.mp4"
                    cached_path = cache_document_from_bytes(raw_bytes, filename)
                    logger.info("[Feishu] Cached message video resource at %s", cached_path)
                    return cached_path, media_type

                if not Path(filename).suffix and media_type in _DOCUMENT_MIME_TO_EXT:
                    filename = f"{filename}{_DOCUMENT_MIME_TO_EXT[media_type]}"
                cached_path = cache_document_from_bytes(raw_bytes, filename)
                logger.info("[Feishu] Cached message document resource at %s", cached_path)
                return cached_path, (media_type or self._guess_document_media_type(filename))
            except Exception:
                logger.warning(
                    "[Feishu] Failed to cache message resource %s/%s",
                    message_id,
                    file_key,
                    exc_info=True,
                )
        return "", ""

    # =========================================================================
    # Static helpers — extension / media-type guessing
    # =========================================================================

    @staticmethod
    def _read_binary_response(response: Any) -> bytes:
        file_obj = getattr(response, "file", None)
        if file_obj is None:
            return b""
        if hasattr(file_obj, "getvalue"):
            return bytes(file_obj.getvalue())
        return bytes(file_obj.read())

    @staticmethod
    def _get_response_header(response: Any, name: str) -> str:
        raw = getattr(response, "raw", None)
        headers = getattr(raw, "headers", {}) or {}
        return str(headers.get(name, headers.get(name.lower(), "")) or "").split(";", 1)[0].strip().lower()

    @staticmethod
    def _guess_extension(filename: str, content_type: str, default: str, *, allowed: set[str]) -> str:
        ext = Path(filename or "").suffix.lower()
        if ext in allowed:
            return ext
        guessed = mimetypes.guess_extension((content_type or "").split(";", 1)[0].strip().lower() or "")
        if guessed in allowed:
            return guessed
        return default

    @staticmethod
    def _normalize_media_type(content_type: str, *, default: str) -> str:
        normalized = (content_type or "").split(";", 1)[0].strip().lower()
        return normalized or default

    @staticmethod
    def _guess_document_media_type(filename: str) -> str:
        ext = Path(filename or "").suffix.lower()
        return SUPPORTED_DOCUMENT_TYPES.get(ext, mimetypes.guess_type(filename or "")[0] or "application/octet-stream")

    @staticmethod
    def _display_name_from_cached_path(path: str) -> str:
        basename = os.path.basename(path)
        parts = basename.split("_", 2)
        display_name = parts[2] if len(parts) >= 3 else basename
        return re.sub(r"[^\w.\- ]", "_", display_name)

    @staticmethod
    def _guess_media_type_from_filename(filename: str) -> str:
        guessed = (mimetypes.guess_type(filename or "")[0] or "").lower()
        if guessed:
            return guessed
        ext = Path(filename or "").suffix.lower()
        if ext in _VIDEO_EXTENSIONS:
            return f"video/{ext.lstrip('.')}"
        if ext in _AUDIO_EXTENSIONS:
            return f"audio/{ext.lstrip('.')}"
        if ext in _IMAGE_EXTENSIONS:
            return FeishuAdapter._default_image_media_type(ext)
        return ""

    @staticmethod
    def _map_chat_type(raw_chat_type: str) -> str:
        normalized = (raw_chat_type or "").strip().lower()
        if normalized == "p2p":
            return "dm"
        if "topic" in normalized or "thread" in normalized or "forum" in normalized:
            return "forum"
        if normalized == "group":
            return "group"
        return "dm"

    @staticmethod
    def _resolve_source_chat_type(*, chat_info: Dict[str, Any], event_chat_type: str) -> str:
        resolved = str(chat_info.get("type") or "").strip().lower()
        if resolved in {"group", "forum"}:
            return resolved
        if event_chat_type == "p2p":
            return "dm"
        return "group"

    async def _resolve_sender_profile(
        self,
        sender_id: Any,
        *,
        is_bot: bool = False,
    ) -> Dict[str, Optional[str]]:
        """Map Feishu's three-tier user IDs onto Hermes' SessionSource fields.

        Preference order for the primary ``user_id`` field:
          1. user_id  (tenant-scoped, most stable — requires permission scope)
          2. open_id  (app-scoped, always available — different per bot app)

        ``user_id_alt`` carries the union_id (developer-scoped, stable across
        all apps by the same developer).  Session-key generation prefers
        user_id_alt when present, so participant isolation stays stable even
        if the primary ID is the app-scoped open_id.
        """
        open_id = getattr(sender_id, "open_id", None) or None
        user_id = getattr(sender_id, "user_id", None) or None
        union_id = getattr(sender_id, "union_id", None) or None
        # Prefer tenant-scoped user_id; fall back to app-scoped open_id.
        primary_id = user_id or open_id
        # The message event's ``user_id`` is not consistently a Contact v3
        # lookup key.  Prefer the app-scoped open_id emitted by the event,
        # then union_id; only use user_id as a last resort.  This mirrors the
        # Contact v3 identifier contract and works for both Feishu and Lark.
        # bot/v3/bots/basic_batch only accepts open_id as well.
        name_lookup_id = open_id or union_id or user_id
        display_name = await self._resolve_sender_name_from_api(
            name_lookup_id, is_bot=is_bot,
        )
        if display_name:
            self._cache_sender_name(
                display_name,
                open_id,
                user_id,
                union_id,
            )
        return {
            "user_id": primary_id,
            "user_name": display_name,
            "user_id_alt": union_id,
        }

    def _cache_sender_name(self, name: str, *sender_ids: Optional[str]) -> None:
        """Cache one resolved display name under every event-provided ID alias."""
        normalized_name = str(name or "").strip()
        if not normalized_name:
            return
        expire_at = time.time() + _FEISHU_SENDER_NAME_TTL_SECONDS
        for sender_id in sender_ids:
            normalized_id = str(sender_id or "").strip()
            if normalized_id:
                self._sender_name_cache[normalized_id] = (normalized_name, expire_at)

    def _get_cached_sender_name(self, sender_id: Optional[str]) -> Optional[str]:
        """Return a cached sender name only while its TTL is still valid."""
        if not sender_id:
            return None
        cached = self._sender_name_cache.get(sender_id)
        if cached is None:
            return None
        name, expire_at = cached
        if time.time() < expire_at:
            return name
        self._sender_name_cache.pop(sender_id, None)
        return None

    async def _resolve_sender_name_from_api(
        self,
        sender_id: Optional[str],
        *,
        is_bot: bool = False,
    ) -> Optional[str]:
        """Bots divert to bot/basic_batch — contact API doesn't return bot names.
        Failures are silent so the pipeline never blocks on name resolution.
        """
        if not sender_id or not self._client:
            return None
        trimmed = sender_id.strip()
        if not trimmed:
            return None
        now = time.time()
        cached_name = self._get_cached_sender_name(trimmed)
        if cached_name is not None:
            return cached_name or None  # "" cached means "known nameless"
        if is_bot:
            names = await self._fetch_bot_names([trimmed])
            if names is None:
                return None
            expire_at = now + _FEISHU_SENDER_NAME_TTL_SECONDS
            for oid, name in names.items():
                self._sender_name_cache[oid] = (name, expire_at)
            hit = self._sender_name_cache.get(trimmed)
            return (hit[0] or None) if hit else None
        try:
            from lark_oapi.api.contact.v3 import GetUserRequest  # lazy import
            if trimmed.startswith("ou_"):
                id_type = "open_id"
            elif trimmed.startswith("on_"):
                id_type = "union_id"
            else:
                id_type = "user_id"
            request = GetUserRequest.builder().user_id(trimmed).user_id_type(id_type).build()
            response = await self._run_blocking(self._client.contact.v3.user.get, request)
            if not response or not response.success():
                return None
            user = getattr(getattr(response, "data", None), "user", None)
            name = (
                getattr(user, "name", None)
                or getattr(user, "display_name", None)
                or getattr(user, "nickname", None)
                or getattr(user, "en_name", None)
            )
            if name and isinstance(name, str):
                name = name.strip()
                if name:
                    self._cache_sender_name(name, trimmed)
                    return name
        except Exception:
            logger.debug("[Feishu] Failed to resolve sender name for %s", sender_id, exc_info=True)
        return None

    async def _fetch_bot_names(self, bot_ids: List[str]) -> Optional[Dict[str, str]]:
        if not self._client or not bot_ids:
            return None
        try:
            req = (
                BaseRequest.builder()
                .http_method(HttpMethod.GET)
                .uri("/open-apis/bot/v3/bots/basic_batch")
                .queries([("bot_ids", oid) for oid in bot_ids])
                .token_types({AccessTokenType.TENANT})
                .build()
            )
            resp = await self._run_blocking(self._client.request, req)
            content = getattr(getattr(resp, "raw", None), "content", None)
            if not content:
                return None
            payload = json.loads(content)
            if payload.get("code") != 0:
                return None
            bots = (payload.get("data") or {}).get("bots") or {}
            return {
                oid: str(info.get("name") or "").strip()
                for oid, info in bots.items()
                if oid
            }
        except Exception:
            logger.debug("[Feishu] Failed to fetch bot names for %s", bot_ids, exc_info=True)
            return None

    async def _fetch_message_text(self, message_id: str) -> Optional[str]:
        if not self._client or not message_id:
            return None
        if message_id in self._message_text_cache:
            self._message_text_cache.move_to_end(message_id)
            return self._message_text_cache[message_id]
        try:
            request = self._build_get_message_request(message_id)
            response = await self._run_blocking(self._client.im.v1.message.get, request)
            if not response or getattr(response, "success", lambda: False)() is False:
                code = getattr(response, "code", "unknown")
                msg = getattr(response, "msg", "message lookup failed")
                logger.warning("[Feishu] Failed to fetch parent message %s: [%s] %s", message_id, code, msg)
                return None
            items = getattr(getattr(response, "data", None), "items", None) or []
            parent = items[0] if items else None
            body = getattr(parent, "body", None)
            msg_type = getattr(parent, "msg_type", "") or ""
            raw_content = getattr(body, "content", "") or ""
            parent_mentions = getattr(parent, "mentions", None) if parent else None
            text = self._extract_text_from_raw_content(
                msg_type=msg_type,
                raw_content=raw_content,
                mentions=parent_mentions,
            )
            self._message_text_cache[message_id] = text
            while len(self._message_text_cache) > _FEISHU_MESSAGE_TEXT_CACHE_SIZE:
                self._message_text_cache.popitem(last=False)
            return text
        except Exception:
            logger.warning("[Feishu] Failed to fetch parent message %s", message_id, exc_info=True)
            return None

    def _extract_text_from_raw_content(
        self,
        *,
        msg_type: str,
        raw_content: str,
        mentions: Optional[Sequence[Any]] = None,
    ) -> Optional[str]:
        normalized = normalize_feishu_message(
            message_type=msg_type,
            raw_content=raw_content,
            mentions=mentions,
            bot=self._bot_identity(),
        )
        if normalized.text_content:
            return normalized.text_content
        placeholder = normalized.metadata.get("placeholder_text") if isinstance(normalized.metadata, dict) else None
        return str(placeholder).strip() or None

    @staticmethod
    def _default_image_media_type(ext: str) -> str:
        normalized_ext = (ext or "").lower()
        if normalized_ext in {".jpg", ".jpeg"}:
            return "image/jpeg"
        return f"image/{normalized_ext.lstrip('.') or 'jpeg'}"

    @staticmethod
    def _log_background_failure(future: Any) -> None:
        try:
            future.result()
        except Exception:
            logger.exception("[Feishu] Background inbound processing failed")

    # =========================================================================
    # Inbound admission
    # =========================================================================

    def _admit(self, sender: Any, message: Any) -> Optional[RejectReason]:
        sender_ids = _sender_identity(sender)
        self_ids = frozenset(v for v in (self._bot_open_id, self._bot_user_id) if v)
        is_bot = _is_bot_sender(sender)
        is_group = getattr(message, "chat_type", "p2p") != "p2p"
        chat_id = getattr(message, "chat_id", "") or ""
        require_mention = is_group and self._require_mention_for(chat_id)

        # Defensive only — Feishu doesn't echo our outbound back as inbound,
        # and open_id is always populated on both sides.
        if self_ids and sender_ids & self_ids:
            return "self_echo"

        if is_bot:
            mode = self._allow_bots
            if mode != "mentions" and mode != "all":
                return "bots_disabled"
            # Defensive: pre-hydration or malformed payloads.
            if not self_ids or not sender_ids:
                return "self_ids_unknown"
            # Step 4 covers mention enforcement for groups when require_mention
            # is on; check here only on paths step 4 won't reach.
            if mode == "mentions" and not require_mention and not self._mentions_self(message):
                return "bot_not_mentioned"

        if not is_group:
            if os.getenv("FEISHU_ALLOW_ALL_USERS", "").strip().lower() in {"true", "1", "yes"}:
                return None
            if os.getenv("GATEWAY_ALLOW_ALL_USERS", "").strip().lower() in {"true", "1", "yes"}:
                return None
            # Empty FEISHU_ALLOWED_USERS is the pairing-mode default from setup:
            # forward DMs to gateway intake so the pairing handshake can run.
            # Gateway auth fail-closes agent access until approval.
            if not self._allowed_group_users:
                return None
            if not (sender_ids and (sender_ids & self._allowed_group_users)):
                return "dm_policy_rejected"
            return None

        if not self._allow_group_message(
            getattr(sender, "sender_id", None), chat_id, is_bot=is_bot,
        ):
            return "group_policy_rejected"
        if require_mention and not self._mentions_self(message):
            return "group_policy_rejected"
        return None

    def _require_mention_for(self, chat_id: str) -> bool:
        rule = self._group_rules.get(chat_id) if chat_id else None
        if rule and rule.require_mention is not None:
            return rule.require_mention
        return self._require_mention

    # --- Group policy ---------------------------------------------------------

    def _allow_group_message(
        self,
        sender_id: Any,
        chat_id: str = "",
        *,
        is_bot: bool = False,
    ) -> bool:
        """Per-group policy gate for non-DM traffic."""
        sender_open_id = getattr(sender_id, "open_id", None)
        sender_user_id = getattr(sender_id, "user_id", None)
        sender_ids = {sender_open_id, sender_user_id} - {None}

        if sender_ids and self._admins and (sender_ids & self._admins):
            return True

        rule = self._group_rules.get(chat_id) if chat_id else None
        if rule:
            policy = rule.policy
            allowlist = rule.allowlist
            blacklist = rule.blacklist
        else:
            policy = self._default_group_policy or self._group_policy
            allowlist = self._allowed_group_users
            blacklist = set()

        # Channel locks apply to everyone; allowlist/blacklist only gate humans
        # (bots were already cleared upstream by FEISHU_ALLOW_BOTS).
        if policy == "disabled":
            return False
        if policy == "open":
            return True
        if policy == "admin_only":
            return False
        if is_bot:
            return True

        if policy == "allowlist":
            return bool(sender_ids and (sender_ids & allowlist))
        if policy == "blacklist":
            return bool(sender_ids and not (sender_ids & blacklist))

        return bool(sender_ids and (sender_ids & self._allowed_group_users))

    # --- Mention detection ----------------------------------------------------

    def _mentions_self(self, message: Any) -> bool:
        # @_all is Feishu's @everyone placeholder.
        raw_content = getattr(message, "content", "") or ""
        if "@_all" in raw_content:
            return True
        mentions = getattr(message, "mentions", None) or []
        if mentions and self._message_mentions_bot(mentions):
            return True
        normalized = normalize_feishu_message(
            message_type=getattr(message, "message_type", "") or "",
            raw_content=raw_content,
            mentions=getattr(message, "mentions", None),
            bot=self._bot_identity(),
        )
        return self._post_mentions_bot(normalized.mentions)

    def _message_mentions_bot(self, mentions: List[Any]) -> bool:
        # IDs trump names: when both sides have open_id (or both user_id),
        # match requires equal IDs. Name fallback only when either side
        # lacks an ID.
        for mention in mentions:
            mention_id = getattr(mention, "id", None)
            mention_open_id = (getattr(mention_id, "open_id", None) or "").strip()
            mention_user_id = (getattr(mention_id, "user_id", None) or "").strip()
            mention_name = (getattr(mention, "name", None) or "").strip()

            if mention_open_id and self._bot_open_id:
                if mention_open_id == self._bot_open_id:
                    return True
                continue  # IDs differ — not the bot; skip name fallback.
            if mention_user_id and self._bot_user_id:
                if mention_user_id == self._bot_user_id:
                    return True
                continue
            if self._bot_name and mention_name == self._bot_name:
                return True

        return False

    def _post_mentions_bot(self, mentions: List[FeishuMentionRef]) -> bool:
        return any(m.is_self for m in mentions)

    def _bot_identity(self) -> _FeishuBotIdentity:
        return _FeishuBotIdentity(
            open_id=self._bot_open_id,
            user_id=self._bot_user_id,
            name=self._bot_name,
        )

    async def _hydrate_bot_identity(self) -> None:
        """Best-effort discovery of bot identity for precise group mention gating
        and self-sent bot event filtering.

        Populates ``_bot_open_id`` and ``_bot_name`` from /open-apis/bot/v3/info
        (no extra scopes required beyond the tenant access token). The probe
        always runs when a client is available so stale env vars from app/bot
        migrations do not break group @mention gating. Falls back to the
        application info endpoint for ``_bot_name`` only when the first probe
        doesn't return it. If the probe fails, env-provided values are preserved.
        """
        if not self._client:
            return

        # Primary probe: /open-apis/bot/v3/info — returns bot_name + open_id, no
        # extra scopes required. This is the same endpoint the onboarding wizard
        # uses via probe_bot().
        try:
            req = (
                BaseRequest.builder()
                .http_method(HttpMethod.GET)
                .uri("/open-apis/bot/v3/info")
                .token_types({AccessTokenType.TENANT})
                .build()
            )
            resp = await self._run_blocking(self._client.request, req)
            content = getattr(getattr(resp, "raw", None), "content", None)
            if content:
                payload = json.loads(content)
                parsed = _parse_bot_response(payload) or {}
                open_id = (parsed.get("bot_open_id") or "").strip()
                bot_name = (parsed.get("bot_name") or "").strip()
                if open_id:
                    if self._bot_open_id and self._bot_open_id != open_id:
                        logger.warning(
                            "[Feishu] FEISHU_BOT_OPEN_ID is stale; using /bot/v3/info open_id for group @mention gating."
                        )
                    self._bot_open_id = open_id
                if bot_name:
                    if self._bot_name and self._bot_name != bot_name:
                        logger.info(
                            "[Feishu] FEISHU_BOT_NAME differs from /bot/v3/info; using hydrated bot name for group @mention gating."
                        )
                    self._bot_name = bot_name
        except Exception:
            logger.debug(
                "[Feishu] /bot/v3/info probe failed during hydration",
                exc_info=True,
            )

        # Fallback probe for _bot_name only: application info endpoint. Needs
        # admin:app.info:readonly or application:application:self_manage scope,
        # so it's best-effort.
        if self._bot_name:
            return
        try:
            request = self._build_get_application_request(app_id=self._app_id, lang="en_us")
            response = await self._run_blocking(self._client.application.v6.application.get, request)
            if not response or not response.success():
                code = getattr(response, "code", None)
                if code == 99991672:
                    logger.warning(
                        "[Feishu] Unable to hydrate bot name from application info. "
                        "Grant admin:app.info:readonly or application:application:self_manage "
                        "so group @mention gating can resolve the bot name precisely."
                    )
                return
            app = getattr(getattr(response, "data", None), "app", None)
            app_name = (getattr(app, "app_name", None) or "").strip()
            if app_name and not self._bot_name:
                self._bot_name = app_name
        except Exception:
            logger.debug("[Feishu] Failed to hydrate bot name from application info", exc_info=True)

    # =========================================================================
    # Deduplication — seen message ID cache (persistent)
    # =========================================================================

    def _load_seen_message_ids(self) -> None:
        try:
            payload = json.loads(self._dedup_state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError):
            logger.warning("[Feishu] Failed to load persisted dedup state from %s", self._dedup_state_path, exc_info=True)
            return
        seen_data = payload.get("message_ids", {}) if isinstance(payload, dict) else {}
        now = time.time()
        ttl = _FEISHU_DEDUP_TTL_SECONDS
        # Backward-compat: old format stored a plain list of IDs (no timestamps).
        if isinstance(seen_data, list):
            entries: Dict[str, float] = {str(item).strip(): 0.0 for item in seen_data if str(item).strip()}
        elif isinstance(seen_data, dict):
            entries = {}
            for key, value in seen_data.items():
                if not isinstance(key, str) or not key.strip():
                    continue
                try:
                    entries[key] = float(value)
                except (TypeError, ValueError):
                    continue
        else:
            return
        # Filter out TTL-expired entries (entries saved with ts=0.0 are treated as immortal
        # for one migration cycle to avoid nuking old data on first upgrade).
        valid: Dict[str, float] = {
            msg_id: ts for msg_id, ts in entries.items()
            if ts == 0.0 or ttl <= 0 or now - ts < ttl
        }
        # Apply size cap; keep the most recently seen IDs.
        sorted_ids = sorted(valid, key=lambda k: valid[k], reverse=True)[:self._dedup_cache_size]
        self._seen_message_order = list(reversed(sorted_ids))
        self._seen_message_ids = {k: valid[k] for k in sorted_ids}

    def _persist_seen_message_ids(self) -> None:
        try:
            self._dedup_state_path.parent.mkdir(parents=True, exist_ok=True)
            recent = self._seen_message_order[-self._dedup_cache_size:]
            # Save as {msg_id: timestamp} so TTL filtering works across restarts.
            payload = {"message_ids": {k: self._seen_message_ids[k] for k in recent if k in self._seen_message_ids}}
            atomic_json_write(self._dedup_state_path, payload, indent=None)
        except OSError:
            logger.warning("[Feishu] Failed to persist dedup state to %s", self._dedup_state_path, exc_info=True)

    def _is_duplicate(self, message_id: str) -> bool:
        now = time.time()
        ttl = _FEISHU_DEDUP_TTL_SECONDS
        with self._dedup_lock:
            seen_at = self._seen_message_ids.get(message_id)
            if seen_at is not None and (ttl <= 0 or now - seen_at < ttl):
                return True
            # Record with current wall-clock timestamp so TTL works across restarts.
            self._seen_message_ids[message_id] = now
            self._seen_message_order.append(message_id)
            while len(self._seen_message_order) > self._dedup_cache_size:
                stale = self._seen_message_order.pop(0)
                self._seen_message_ids.pop(stale, None)
            self._persist_seen_message_ids()
            return False

    # =========================================================================
    # Outbound payload construction and send pipeline
    # =========================================================================

    def _build_outbound_payload(
        self, content: str, *, prefer_post: bool = False,
    ) -> tuple[str, str]:
        # Empirically (issue #52786), current Feishu clients render markdown
        # tables inside ``post``-type ``md`` elements natively. The previous
        # table-downgrade branch forced any table-containing message to
        # ``text``, which left Feishu readers seeing the raw pipe-and-dash
        # source instead of a rendered table. Trust the common markdown path
        # for table content too.
        #
        # ``prefer_post`` lets ``send`` treat the chunk as part of a larger
        # markdown document: when a long markdown reply is split at
        # MAX_MESSAGE_LENGTH, the per-chunk regex would otherwise
        # mis-classify a plain-prose chunk as ``text``. See #26841.
        if prefer_post or _MARKDOWN_HINT_RE.search(content):
            return "post", _build_markdown_post_payload(content)
        text_payload = {"text": content}
        return "text", json.dumps(text_payload, ensure_ascii=False)

    @staticmethod
    def _get_audio_duration_ms(file_path: str) -> int:
        """Extract OGG/Opus audio duration in milliseconds (pure Python, no deps).

        Parses the OGG container to find the last granule position and divides
        by the Opus sample rate (48000 Hz). Returns 0 for non-OGG files or on error.
        """
        import struct
        try:
            with open(file_path, "rb") as f:
                data = f.read()
            pos = 0
            last_granule = 0
            while pos < len(data) - 27:
                idx = data.find(b"OggS", pos)
                if idx == -1:
                    break
                pos = idx
                if pos + 27 > len(data):
                    break
                granule = struct.unpack_from("<q", data, pos + 6)[0]
                num_segments = data[pos + 26]
                if granule > 0:
                    last_granule = granule
                segment_end = pos + 27 + num_segments
                if segment_end > len(data):
                    break
                page_size = num_segments
                for i in range(num_segments):
                    page_size += data[pos + 27 + i]
                pos += page_size
            return int(last_granule / 48000 * 1000) if last_granule > 0 else 0
        except Exception:
            return 0

    async def _send_uploaded_file_message(
        self,
        *,
        chat_id: str,
        file_path: str,
        reply_to: Optional[str],
        metadata: Optional[Dict[str, Any]],
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        outbound_message_type: str = "file",
    ) -> SendResult:
        if not self._client:
            return SendResult(success=False, error="Not connected")
        if not os.path.exists(file_path):
            return SendResult(success=False, error=f"File not found: {file_path}")

        display_name = file_name or os.path.basename(file_path)
        upload_file_type, resolved_message_type = self._resolve_outbound_file_routing(
            file_path=display_name,
            requested_message_type=outbound_message_type,
        )
        try:
            duration_ms = 0
            if upload_file_type == "opus":
                duration_ms = self._get_audio_duration_ms(file_path)
            with open(file_path, "rb") as file_obj:
                body = self._build_file_upload_body(
                    file_type=upload_file_type,
                    file_name=display_name,
                    file=file_obj,
                    duration=duration_ms,
                )
                request = self._build_file_upload_request(body)
                upload_response = await self._run_blocking(self._client.im.v1.file.create, request)
            file_key = self._extract_response_field(upload_response, "file_key")
            if not file_key:
                return self._response_error_result(
                    upload_response,
                    default_message="file upload failed",
                    override_error="Feishu file upload missing file_key",
                )

            if caption:
                media_tag = {
                    "tag": "media",
                    "file_key": file_key,
                    "file_name": display_name,
                }
                message_response = await self._send_attachment_message(
                    chat_id=chat_id,
                    msg_type="post",
                    payload=self._build_media_post_payload(caption=caption, media_tag=media_tag),
                    reply_to=reply_to,
                    metadata=metadata,
                )
            else:
                message_response = await self._send_attachment_message(
                    chat_id=chat_id,
                    msg_type=resolved_message_type,
                    payload=json.dumps({"file_key": file_key}, ensure_ascii=False),
                    reply_to=reply_to,
                    metadata=metadata,
                )
            return self._finalize_send_result(message_response, "file send failed")
        except Exception as exc:
            logger.error("[Feishu] Failed to send file %s: %s", file_path, exc, exc_info=True)
            return SendResult(success=False, error=str(exc))

    async def _fetch_last_message_in_thread(self, thread_id: str) -> Optional[str]:
        """Fetch the last message_id in a thread for reply-based routing."""
        if not self._client or not thread_id:
            return None
        if self._is_message_thread_anchor(thread_id):
            return str(thread_id)
        try:
            from lark_oapi.api.im.v1 import ListMessageRequest
            request = (
                ListMessageRequest.builder()
                .container_id_type("thread")
                .container_id(thread_id)
                .page_size(1)
                .build()
            )
            response = await asyncio.to_thread(self._client.im.v1.message.list, request)
            if response and getattr(response, "success", lambda: False)():
                items = getattr(getattr(response, "data", None), "items", None)
                if items and len(items) > 0:
                    return getattr(items[0], "message_id", None)
        except Exception as exc:
            logger.debug("[Feishu] Failed to fetch last message in thread %s: %s", thread_id, exc)
        return None

    async def _send_attachment_message(
        self,
        *,
        chat_id: str,
        msg_type: str,
        payload: str,
        reply_to: Optional[str],
        metadata: Optional[Dict[str, Any]],
    ) -> Any:
        """Deliver an already-uploaded attachment, surviving topic routing.

        Inside a topic the normal send path keys the message on
        ``receive_id_type=thread_id``.  Feishu accepts text and cards that
        way but rejects every attachment kind — audio, file, media, image,
        and a post carrying one — with a bare field-validation error, so a
        file requested in a topic used to be lost while the same request in
        the main chat worked.  The reply API places the same upload in the
        topic without complaint, so re-anchor on a message inside the topic
        first; only if that also fails do we drop the topic and post flat in
        the chat, on the grounds that a file in the wrong place still beats
        no file.
        """
        response = await self._feishu_send_with_retry(
            chat_id=chat_id,
            msg_type=msg_type,
            payload=payload,
            reply_to=reply_to,
            metadata=metadata,
        )
        thread_id = (metadata or {}).get("thread_id")
        if (
            self._response_succeeded(response)
            or getattr(response, "code", None) != _FEISHU_ATTACHMENT_THREAD_RECEIVE_CODE
            or not thread_id
        ):
            return response

        anchor_id = (metadata or {}).get("reply_to_message_id") or await self._fetch_last_message_in_thread(
            str(thread_id)
        )
        if anchor_id and anchor_id != reply_to:
            logger.info(
                "[Feishu] %s rejected in topic %s; retrying as a reply to %s",
                msg_type, thread_id, anchor_id,
            )
            response = await self._feishu_send_with_retry(
                chat_id=chat_id,
                msg_type=msg_type,
                payload=payload,
                reply_to=anchor_id,
                metadata=metadata,
            )
            if self._response_succeeded(response):
                return response
        logger.warning(
            "[Feishu] topic %s would not take a %s (no usable reply anchor, or the "
            "reply was rejected too); delivering it flat in chat %s",
            thread_id, msg_type, chat_id,
        )
        return await self._feishu_send_with_retry(
            chat_id=chat_id,
            msg_type=msg_type,
            payload=payload,
            reply_to=None,
            metadata=None,
        )

    async def _send_raw_message(
        self,
        *,
        chat_id: str,
        msg_type: str,
        payload: str,
        reply_to: Optional[str],
        metadata: Optional[Dict[str, Any]],
    ) -> Any:
        metadata_thread_id = (metadata or {}).get("thread_id")
        effective_reply_to = reply_to
        if not effective_reply_to and metadata_thread_id:
            effective_reply_to = (metadata or {}).get("reply_to_message_id")
            if not effective_reply_to and self._is_message_thread_anchor(metadata_thread_id):
                effective_reply_to = str(metadata_thread_id)
        reply_in_thread = bool(metadata_thread_id)
        if effective_reply_to:
            body = self._build_reply_message_body(
                content=payload,
                msg_type=msg_type,
                reply_in_thread=reply_in_thread,
                uuid_value=str(uuid.uuid4()),
            )
            request = self._build_reply_message_request(effective_reply_to, body)
            return await self._run_blocking(self._client.im.v1.message.reply, request)

        # For topic/thread messages that fell back from reply→create, use
        # thread_id as receive_id so the message lands in the topic instead of
        # the main chat.
        _thread_id = metadata_thread_id
        if _thread_id:
            body = self._build_create_message_body(
                receive_id=_thread_id,
                msg_type=msg_type,
                content=payload,
                uuid_value=str(uuid.uuid4()),
            )
            request = self._build_create_message_request("thread_id", body)
        else:
            receive_id = chat_id
            receive_id_type = "chat_id"
            if chat_id.startswith("feishu_user_id:"):
                receive_id = chat_id.split(":", 1)[1]
                receive_id_type = "user_id"
            elif chat_id.startswith("ou_"):
                receive_id_type = "open_id"

            body = self._build_create_message_body(
                receive_id=receive_id,
                msg_type=msg_type,
                content=payload,
                uuid_value=str(uuid.uuid4()),
            )
            request = self._build_create_message_request(receive_id_type, body)
        return await self._run_blocking(self._client.im.v1.message.create, request)

    @staticmethod
    def _response_succeeded(response: Any) -> bool:
        return bool(response and getattr(response, "success", lambda: False)())

    @staticmethod
    def _extract_response_field(response: Any, field_name: str) -> Any:
        if not FeishuAdapter._response_succeeded(response):
            return None
        data = getattr(response, "data", None)
        return getattr(data, field_name, None) if data else None

    def _response_error_result(
        self,
        response: Any,
        *,
        default_message: str,
        override_error: Optional[str] = None,
    ) -> SendResult:
        if override_error:
            return SendResult(success=False, error=override_error, raw_response=response)
        code = getattr(response, "code", "unknown")
        msg = getattr(response, "msg", default_message)
        return SendResult(success=False, error=f"[{code}] {msg}", raw_response=response)

    def _finalize_send_result(self, response: Any, default_message: str) -> SendResult:
        if not self._response_succeeded(response):
            return self._response_error_result(response, default_message=default_message)
        return SendResult(
            success=True,
            message_id=self._extract_response_field(response, "message_id"),
            raw_response=response,
        )

    # =========================================================================
    # Connection internals — websocket / webhook setup
    # =========================================================================

    async def _connect_with_retry(self) -> None:
        for attempt in range(_FEISHU_CONNECT_ATTEMPTS):
            try:
                if self._connection_mode == "websocket":
                    await self._connect_websocket()
                else:
                    await self._connect_webhook()
                return
            except Exception as exc:
                self._running = False
                self._disable_websocket_auto_reconnect()
                self._ws_future = None
                await self._stop_webhook_server()
                if attempt >= _FEISHU_CONNECT_ATTEMPTS - 1:
                    raise
                wait_seconds = 2 ** attempt
                logger.warning(
                    "[Feishu] Connect attempt %d/%d failed; retrying in %ds: %s",
                    attempt + 1,
                    _FEISHU_CONNECT_ATTEMPTS,
                    wait_seconds,
                    exc,
                )
                await asyncio.sleep(wait_seconds)

    async def _connect_websocket(self) -> None:
        if not FEISHU_WEBSOCKET_AVAILABLE:
            raise RuntimeError("websockets not installed; websocket mode unavailable")
        domain = FEISHU_DOMAIN if self._domain_name != "lark" else LARK_DOMAIN
        self._client = self._build_lark_client(domain)
        self._event_handler = self._build_event_handler()
        if self._event_handler is None:
            raise RuntimeError("failed to build Feishu event handler")
        loop = self._loop
        if loop is None or loop.is_closed():
            raise RuntimeError("adapter loop is not ready")
        await self._hydrate_bot_identity()
        self._ws_client = self._build_websocket_client(
            domain=domain,
            event_handler=self._event_handler,
        )
        self._ws_future = loop.run_in_executor(
            None,
            _run_official_feishu_ws_client,
            self._ws_client,
            self,
        )

    def _build_websocket_client(self, *, domain: str, event_handler: Any) -> Any:
        """Create a WebSocket client across supported lark-oapi versions.

        ``extra_ua_tags`` enables Channel delivery on current SDKs.  Older
        installations reject that keyword before constructing the client, so
        retain a connection-capable fallback and make the missing group-event
        capability explicit in the log rather than failing the whole adapter.
        """
        kwargs = {
            "app_id": self._app_id,
            "app_secret": self._app_secret,
            "log_level": lark.LogLevel.INFO,
            "event_handler": event_handler,
            "domain": domain,
        }
        try:
            # Channel SDK signaling tag: without this UA tag the Feishu
            # server does not push group @mention events over the WebSocket
            # transport.  The tag tells the server to use the Channel protocol
            # which enables group-message routing in addition to P2P DM.
            # See https://github.com/NousResearch/hermes-agent/issues/50656
            return FeishuWSClient(**kwargs, extra_ua_tags=["channel"])
        except TypeError as exc:
            if "extra_ua_tags" not in str(exc) or "unexpected keyword" not in str(exc):
                raise
            logger.warning(
                "[Feishu] lark-oapi does not support extra_ua_tags; connecting without "
                "Channel group-event signaling. Upgrade to lark-oapi==1.6.8 for group @mentions."
            )
            return FeishuWSClient(**kwargs)

    async def _connect_webhook(self) -> None:
        if not FEISHU_WEBHOOK_AVAILABLE:
            raise RuntimeError("aiohttp not installed; webhook mode unavailable")
        domain = FEISHU_DOMAIN if self._domain_name != "lark" else LARK_DOMAIN
        self._client = self._build_lark_client(domain)
        self._event_handler = self._build_event_handler()
        if self._event_handler is None:
            raise RuntimeError("failed to build Feishu event handler")
        await self._hydrate_bot_identity()
        # client_max_size backstops the bounded reader in
        # _handle_webhook_request; aiohttp then enforces the same cap on
        # every read path (#58536/#58902/#59180 pattern).
        app = web.Application(client_max_size=_FEISHU_WEBHOOK_MAX_BODY_BYTES)
        app.router.add_post(self._webhook_path, self._handle_webhook_request)
        self._webhook_runner = web.AppRunner(app)
        await self._webhook_runner.setup()
        self._webhook_site = web.TCPSite(self._webhook_runner, self._webhook_host, self._webhook_port)
        await self._webhook_site.start()

    def _build_lark_client(self, domain: Any) -> Any:
        return (
            lark.Client.builder()
            .app_id(self._app_id)
            .app_secret(self._app_secret)
            .domain(domain)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )

    async def _feishu_send_with_retry(
        self,
        *,
        chat_id: str,
        msg_type: str,
        payload: str,
        reply_to: Optional[str],
        metadata: Optional[Dict[str, Any]],
    ) -> Any:
        last_error: Optional[Exception] = None
        active_reply_to = reply_to
        active_metadata = metadata
        auto_thread_root = str((metadata or {}).get("thread_id") or "")
        pending_roots = self._auto_thread_state("_pending_auto_thread_roots")
        failed_roots = self._auto_thread_state("_failed_auto_thread_roots")
        auto_thread_pending = bool(
            self._is_message_thread_anchor(auto_thread_root)
            and auto_thread_root in pending_roots
        )
        if auto_thread_root in failed_roots:
            active_reply_to = None
            active_metadata = None
            auto_thread_root = ""
        for attempt in range(_FEISHU_SEND_ATTEMPTS):
            try:
                response = await self._send_raw_message(
                    chat_id=chat_id,
                    msg_type=msg_type,
                    payload=payload,
                    reply_to=active_reply_to,
                    metadata=active_metadata,
                )
                if auto_thread_pending:
                    if self._response_succeeded(response):
                        self._mark_auto_thread_established(auto_thread_root)
                        return response
                    if (
                        msg_type == "post"
                        and _POST_CONTENT_INVALID_RE.search(
                            str(getattr(response, "msg", "") or "")
                        )
                    ):
                        # Let send() retry this payload as plain text while the
                        # root remains pending; formatting rejection is not a
                        # topic-creation failure.
                        return response
                    logger.warning(
                        "[Feishu] Auto-thread reply to %s failed (code %s); "
                        "falling back to one flat message in chat %s",
                        auto_thread_root,
                        getattr(response, "code", None),
                        chat_id,
                    )
                    self._mark_auto_thread_failed(auto_thread_root)
                    return await self._send_raw_message(
                        chat_id=chat_id,
                        msg_type=msg_type,
                        payload=payload,
                        reply_to=None,
                        metadata=None,
                    )
                # If replying to a message failed because it was withdrawn or not found,
                # fall back to posting a new message directly to the chat.
                if active_reply_to and not self._response_succeeded(response):
                    code = getattr(response, "code", None)
                    if code in _FEISHU_REPLY_FALLBACK_CODES:
                        if (active_metadata or {}).get("thread_id"):
                            logger.warning(
                                "[Feishu] Reply to %s failed in thread %s (code %s — message withdrawn/missing); "
                                "skipping top-level fallback to avoid creating a new topic",
                                active_reply_to,
                                (active_metadata or {}).get("thread_id"),
                                code,
                            )
                            return response
                        logger.warning(
                            "[Feishu] Reply to %s failed (code %s — message withdrawn/missing); "
                            "falling back to new message in chat %s",
                            active_reply_to,
                            code,
                            chat_id,
                        )
                        active_reply_to = None
                        response = await self._send_raw_message(
                            chat_id=chat_id,
                            msg_type=msg_type,
                            payload=payload,
                            reply_to=None,
                            metadata=active_metadata,
                        )
                return response
            except Exception as exc:
                last_error = exc
                if msg_type == "post" and _POST_CONTENT_INVALID_RE.search(str(exc)):
                    raise
                if attempt >= _FEISHU_SEND_ATTEMPTS - 1:
                    if auto_thread_pending:
                        logger.warning(
                            "[Feishu] Auto-thread reply to %s raised after retries; "
                            "falling back to one flat message in chat %s: %s",
                            auto_thread_root,
                            chat_id,
                            exc,
                        )
                        self._mark_auto_thread_failed(auto_thread_root)
                        return await self._send_raw_message(
                            chat_id=chat_id,
                            msg_type=msg_type,
                            payload=payload,
                            reply_to=None,
                            metadata=None,
                        )
                    raise
                wait_seconds = 2 ** attempt
                logger.warning(
                    "[Feishu] Send attempt %d/%d failed for chat %s; retrying in %ds: %s",
                    attempt + 1,
                    _FEISHU_SEND_ATTEMPTS,
                    chat_id,
                    wait_seconds,
                    exc,
                )
                await asyncio.sleep(wait_seconds)
        raise last_error or RuntimeError("Feishu send failed")

    async def _release_app_lock(self) -> None:
        if not self._app_lock_identity:
            return
        try:
            release_scoped_lock(_FEISHU_APP_LOCK_SCOPE, self._app_lock_identity)
        except Exception as exc:
            logger.warning("[Feishu] Failed to release app lock: %s", exc, exc_info=True)
        finally:
            self._app_lock_identity = None

    # =========================================================================
    # Lark API request builders
    # =========================================================================

    @staticmethod
    def _build_get_chat_request(chat_id: str) -> Any:
        if GetChatRequest is not None:
            return GetChatRequest.builder().chat_id(chat_id).build()
        return SimpleNamespace(chat_id=chat_id)

    @staticmethod
    def _build_get_message_request(message_id: str) -> Any:
        if GetMessageRequest is not None:
            return GetMessageRequest.builder().message_id(message_id).build()
        return SimpleNamespace(message_id=message_id)

    @staticmethod
    def _build_message_resource_request(*, message_id: str, file_key: str, resource_type: str) -> Any:
        if GetMessageResourceRequest is not None:
            return (
                GetMessageResourceRequest.builder()
                .message_id(message_id)
                .file_key(file_key)
                .type(resource_type)
                .build()
            )
        return SimpleNamespace(message_id=message_id, file_key=file_key, type=resource_type)

    @staticmethod
    def _build_get_application_request(*, app_id: str, lang: str) -> Any:
        if GetApplicationRequest is not None:
            return (
                GetApplicationRequest.builder()
                .app_id(app_id)
                .lang(lang)
                .build()
            )
        return SimpleNamespace(app_id=app_id, lang=lang)

    @staticmethod
    def _build_reply_message_body(*, content: str, msg_type: str, reply_in_thread: bool, uuid_value: str) -> Any:
        if ReplyMessageRequestBody is not None:
            return (
                ReplyMessageRequestBody.builder()
                .content(content)
                .msg_type(msg_type)
                .reply_in_thread(reply_in_thread)
                .uuid(uuid_value)
                .build()
            )
        return SimpleNamespace(
            content=content,
            msg_type=msg_type,
            reply_in_thread=reply_in_thread,
            uuid=uuid_value,
        )

    @staticmethod
    def _build_reply_message_request(message_id: str, request_body: Any) -> Any:
        if ReplyMessageRequest is not None:
            return (
                ReplyMessageRequest.builder()
                .message_id(message_id)
                .request_body(request_body)
                .build()
            )
        return SimpleNamespace(message_id=message_id, request_body=request_body)

    @staticmethod
    def _build_update_message_body(*, msg_type: str, content: str) -> Any:
        if UpdateMessageRequestBody is not None:
            return (
                UpdateMessageRequestBody.builder()
                .msg_type(msg_type)
                .content(content)
                .build()
            )
        return SimpleNamespace(msg_type=msg_type, content=content)

    @staticmethod
    def _build_update_message_request(message_id: str, request_body: Any) -> Any:
        if UpdateMessageRequest is not None:
            return (
                UpdateMessageRequest.builder()
                .message_id(message_id)
                .request_body(request_body)
                .build()
            )
        return SimpleNamespace(message_id=message_id, request_body=request_body)

    @staticmethod
    def _build_create_message_body(*, receive_id: str, msg_type: str, content: str, uuid_value: str) -> Any:
        if CreateMessageRequestBody is not None:
            return (
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type(msg_type)
                .content(content)
                .uuid(uuid_value)
                .build()
            )
        return SimpleNamespace(
            receive_id=receive_id,
            msg_type=msg_type,
            content=content,
            uuid=uuid_value,
        )

    @staticmethod
    def _build_create_message_request(receive_id_type: str, request_body: Any) -> Any:
        if CreateMessageRequest is not None:
            return (
                CreateMessageRequest.builder()
                .receive_id_type(receive_id_type)
                .request_body(request_body)
                .build()
            )
        return SimpleNamespace(receive_id_type=receive_id_type, request_body=request_body)

    @staticmethod
    def _build_image_upload_body(*, image_type: str, image: Any) -> Any:
        if CreateImageRequestBody is not None:
            return (
                CreateImageRequestBody.builder()
                .image_type(image_type)
                .image(image)
                .build()
            )
        return SimpleNamespace(image_type=image_type, image=image)

    @staticmethod
    def _build_image_upload_request(request_body: Any) -> Any:
        if CreateImageRequest is not None:
            return CreateImageRequest.builder().request_body(request_body).build()
        return SimpleNamespace(request_body=request_body)

    @staticmethod
    def _build_file_upload_body(*, file_type: str, file_name: str, file: Any, duration: int = 0) -> Any:
        if CreateFileRequestBody is not None:
            builder = (
                CreateFileRequestBody.builder()
                .file_type(file_type)
                .file_name(file_name)
                .file(file)
            )
            if duration > 0:
                builder = builder.duration(duration)
            return builder.build()
        return SimpleNamespace(file_type=file_type, file_name=file_name, file=file, duration=duration)

    @staticmethod
    def _build_file_upload_request(request_body: Any) -> Any:
        if CreateFileRequest is not None:
            return CreateFileRequest.builder().request_body(request_body).build()
        return SimpleNamespace(request_body=request_body)

    def _build_post_payload(self, content: str) -> str:
        return _build_markdown_post_payload(content)

    def _build_media_post_payload(self, *, caption: str, media_tag: Dict[str, str]) -> str:
        payload = json.loads(self._build_post_payload(caption))
        content = payload.setdefault("zh_cn", {}).setdefault("content", [])
        content.append([media_tag])
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _resolve_outbound_file_routing(
        *,
        file_path: str,
        requested_message_type: str,
    ) -> tuple[str, str]:
        ext = Path(file_path).suffix.lower()

        if ext in _FEISHU_OPUS_UPLOAD_EXTENSIONS:
            return "opus", "audio"

        if ext in _FEISHU_MEDIA_UPLOAD_EXTENSIONS:
            return "mp4", "media"

        if ext in _FEISHU_DOC_UPLOAD_TYPES:
            return _FEISHU_DOC_UPLOAD_TYPES[ext], "file"

        if requested_message_type == "file":
            return _FEISHU_FILE_UPLOAD_TYPE, "file"

        return _FEISHU_FILE_UPLOAD_TYPE, "file"


# =============================================================================
# QR scan-to-create onboarding
#
# Device-code flow: user scans a QR code with Feishu/Lark mobile app and the
# platform creates a fully configured bot application automatically.
# Called by `hermes gateway setup` via _setup_feishu() in hermes_cli/gateway.py.
# =============================================================================


def _accounts_base_url(domain: str) -> str:
    return _ONBOARD_ACCOUNTS_URLS.get(domain, _ONBOARD_ACCOUNTS_URLS["feishu"])


def _onboard_open_base_url(domain: str) -> str:
    return _ONBOARD_OPEN_URLS.get(domain, _ONBOARD_OPEN_URLS["feishu"])


def _post_registration(base_url: str, body: Dict[str, str]) -> dict:
    """POST form-encoded data to the registration endpoint, return parsed JSON.

    The registration endpoint returns JSON even on 4xx (e.g. poll returns
    authorization_pending as a 400). We always parse the body regardless of
    HTTP status.
    """
    url = f"{base_url}{_REGISTRATION_PATH}"
    data = urlencode(body).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(req, timeout=_ONBOARD_REQUEST_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        body_bytes = exc.read()
        if body_bytes:
            try:
                return json.loads(body_bytes.decode("utf-8"))
            except (ValueError, json.JSONDecodeError):
                raise exc from None
        raise


def _init_registration(domain: str = "feishu") -> None:
    """Verify the environment supports client_secret auth.

    Raises RuntimeError if not supported.
    """
    base_url = _accounts_base_url(domain)
    res = _post_registration(base_url, {"action": "init"})
    methods = res.get("supported_auth_methods") or []
    if "client_secret" not in methods:
        raise RuntimeError(
            f"Feishu / Lark registration environment does not support client_secret auth. "
            f"Supported: {methods}"
        )


def _begin_registration(domain: str = "feishu") -> dict:
    """Start the device-code flow. Returns device_code, qr_url, user_code, interval, expire_in."""
    base_url = _accounts_base_url(domain)
    res = _post_registration(base_url, {
        "action": "begin",
        "archetype": "PersonalAgent",
        "auth_method": "client_secret",
        "request_user_info": "open_id",
    })
    device_code = res.get("device_code")
    if not device_code:
        raise RuntimeError("Feishu / Lark registration did not return a device_code")
    qr_url = res.get("verification_uri_complete", "")
    if "?" in qr_url:
        qr_url += "&from=hermes&tp=hermes"
    else:
        qr_url += "?from=hermes&tp=hermes"
    return {
        "device_code": device_code,
        "qr_url": qr_url,
        "user_code": res.get("user_code", ""),
        "interval": res.get("interval") or 5,
        "expire_in": res.get("expire_in") or 600,
    }


def _poll_registration(
    *,
    device_code: str,
    interval: int,
    expire_in: int,
    domain: str = "feishu",
) -> Optional[dict]:
    """Poll until the user scans the QR code, or timeout/denial.

    Returns dict with app_id, app_secret, domain, open_id on success.
    Returns None on failure.
    """
    deadline = time.monotonic() + expire_in
    current_domain = domain
    domain_switched = False
    poll_count = 0

    while time.monotonic() < deadline:
        base_url = _accounts_base_url(current_domain)
        try:
            res = _post_registration(base_url, {
                "action": "poll",
                "device_code": device_code,
                "tp": "ob_app",
            })
        except (URLError, OSError, json.JSONDecodeError):
            time.sleep(interval)
            continue

        poll_count += 1
        if poll_count == 1:
            print("  Fetching configuration results...", end="", flush=True)
        elif poll_count % 6 == 0:
            print(".", end="", flush=True)

        # Domain auto-detection
        user_info = res.get("user_info") or {}
        tenant_brand = user_info.get("tenant_brand")
        if tenant_brand == "lark" and not domain_switched:
            current_domain = "lark"
            domain_switched = True
            # Fall through — server may return credentials in this same response.

        # Success
        if res.get("client_id") and res.get("client_secret"):
            if poll_count > 0:
                print()  # newline after "Fetching configuration results..." dots
            return {
                "app_id": res["client_id"],
                "app_secret": res["client_secret"],
                "domain": current_domain,
                "open_id": user_info.get("open_id"),
            }

        # Terminal errors
        error = res.get("error", "")
        if error in {"access_denied", "expired_token"}:
            if poll_count > 0:
                print()
            logger.warning("[Feishu onboard] Registration %s", error)
            return None

        # authorization_pending or unknown — keep polling
        time.sleep(interval)

    if poll_count > 0:
        print()
    logger.warning("[Feishu onboard] Poll timed out after %ds", expire_in)
    return None


try:
    import qrcode as _qrcode_mod
except (ImportError, TypeError):
    _qrcode_mod = None  # type: ignore[assignment]


def _render_qr(url: str) -> bool:
    """Try to render a QR code in the terminal. Returns True if successful."""
    if _qrcode_mod is None:
        return False
    try:
        qr = _qrcode_mod.QRCode()
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
        return True
    except Exception:
        return False


def probe_bot(app_id: str, app_secret: str, domain: str) -> Optional[dict]:
    """Verify bot connectivity via /open-apis/bot/v3/info.

    Uses lark_oapi SDK when available, falls back to raw HTTP otherwise.
    Returns {"bot_name": ..., "bot_open_id": ...} on success, None on failure.

    Note: ``bot_open_id`` here is the bot's app-scoped open_id — the same ID
    that Feishu puts in @mention payloads.  It is NOT the app_id.
    """
    # The SDK import is deferred until connect(); onboarding runs before any
    # connect, so load it here to keep the SDK probe path reachable rather
    # than silently degrading every setup run to the HTTP fallback.
    if _load_lark_oapi():
        return _probe_bot_sdk(app_id, app_secret, domain)
    return _probe_bot_http(app_id, app_secret, domain)


def _build_onboard_client(app_id: str, app_secret: str, domain: str) -> Any:
    """Build a lark Client for the given credentials and domain."""
    sdk_domain = LARK_DOMAIN if domain == "lark" else FEISHU_DOMAIN
    return (
        lark.Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
        .domain(sdk_domain)
        .log_level(lark.LogLevel.WARNING)
        .build()
    )


def _parse_bot_response(data: dict) -> Optional[dict]:
    # /bot/v3/info returns bot.app_name; legacy paths used bot_name — accept both.
    if data.get("code") != 0:
        return None
    bot = data.get("bot") or data.get("data", {}).get("bot") or {}
    return {
        "bot_name": bot.get("app_name") or bot.get("bot_name"),
        "bot_open_id": bot.get("open_id"),
    }


def _probe_bot_sdk(app_id: str, app_secret: str, domain: str) -> Optional[dict]:
    """Probe bot info using lark_oapi SDK."""
    try:
        client = _build_onboard_client(app_id, app_secret, domain)
        req = (
            BaseRequest.builder()
            .http_method(HttpMethod.GET)
            .uri("/open-apis/bot/v3/info")
            .token_types({AccessTokenType.TENANT})
            .build()
        )
        resp = client.request(req)
        content = getattr(getattr(resp, "raw", None), "content", None)
        if content is None:
            return None
        return _parse_bot_response(json.loads(content))
    except Exception as exc:
        logger.debug("[Feishu onboard] SDK probe failed: %s", exc)
        return None


def _probe_bot_http(app_id: str, app_secret: str, domain: str) -> Optional[dict]:
    """Fallback probe using raw HTTP (when lark_oapi is not installed)."""
    base_url = _onboard_open_base_url(domain)
    try:
        token_data = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode("utf-8")
        token_req = Request(
            f"{base_url}/open-apis/auth/v3/tenant_access_token/internal",
            data=token_data,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(token_req, timeout=_ONBOARD_REQUEST_TIMEOUT_S) as resp:
            token_res = json.loads(resp.read().decode("utf-8"))

        access_token = token_res.get("tenant_access_token")
        if not access_token:
            return None

        bot_req = Request(
            f"{base_url}/open-apis/bot/v3/info",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )
        with urlopen(bot_req, timeout=_ONBOARD_REQUEST_TIMEOUT_S) as resp:
            bot_res = json.loads(resp.read().decode("utf-8"))

        return _parse_bot_response(bot_res)
    except (URLError, OSError, KeyError, json.JSONDecodeError) as exc:
        logger.debug("[Feishu onboard] HTTP probe failed: %s", exc)
        return None


def qr_register(
    *,
    initial_domain: str = "feishu",
    timeout_seconds: int = 600,
) -> Optional[dict]:
    """Run the Feishu / Lark scan-to-create QR registration flow.

    Returns on success::

        {
            "app_id": str,
            "app_secret": str,
            "domain": "feishu" | "lark",
            "open_id": str | None,
            "bot_name": str | None,
            "bot_open_id": str | None,
        }

    Returns None on expected failures (network, auth denied, timeout).
    Unexpected errors (bugs, protocol regressions) propagate to the caller.
    """
    try:
        return _qr_register_inner(initial_domain=initial_domain, timeout_seconds=timeout_seconds)
    except (RuntimeError, URLError, OSError, json.JSONDecodeError) as exc:
        logger.warning("[Feishu onboard] Registration failed: %s", exc)
        return None


def _qr_register_inner(
    *,
    initial_domain: str,
    timeout_seconds: int,
) -> Optional[dict]:
    """Run init → begin → poll → probe. Raises on network/protocol errors."""
    print("  Connecting to Feishu / Lark...", end="", flush=True)
    _init_registration(initial_domain)
    begin = _begin_registration(initial_domain)
    print(" done.")

    print()
    qr_url = begin["qr_url"]
    if _render_qr(qr_url):
        print(f"\n  Scan the QR code above, or open this URL directly:\n  {qr_url}")
    else:
        print(f"  Open this URL in Feishu / Lark on your phone:\n\n  {qr_url}\n")
        print("  Tip: pip install qrcode  to display a scannable QR code here next time")
    print()

    result = _poll_registration(
        device_code=begin["device_code"],
        interval=begin["interval"],
        expire_in=min(begin["expire_in"], timeout_seconds),
        domain=initial_domain,
    )
    if not result:
        return None

    # Probe bot — best-effort, don't fail the registration
    bot_info = probe_bot(result["app_id"], result["app_secret"], result["domain"])
    if bot_info:
        result["bot_name"] = bot_info.get("bot_name")
        result["bot_open_id"] = bot_info.get("bot_open_id")
    else:
        result["bot_name"] = None
        result["bot_open_id"] = None

    return result


# ──────────────────────────────────────────────────────────────────────────
# Plugin migration glue (#41112 / #3823)
#
# Added when the Feishu adapter (+ its feishu_comment / feishu_comment_rules /
# feishu_meeting_invite satellites) moved from gateway/platforms/ into this
# bundled plugin. Mirrors the Discord (#24356) / Slack migrations: a
# register(ctx) entry point plus hook implementations that replace the
# per-platform core touchpoints (the Platform.FEISHU elif in gateway/run.py,
# the feishu_cfg YAML→env block + _PLATFORM_CONNECTED_CHECKERS entry in
# gateway/config.py, the _setup_feishu wizard + _PLATFORMS["feishu"] static
# dict in hermes_cli/gateway.py, and the _send_feishu dispatch in
# tools/send_message_tool.py).
# ──────────────────────────────────────────────────────────────────────────

_MIGRATION_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_MIGRATION_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".3gp"}
_MIGRATION_AUDIO_EXTS = {".ogg", ".opus", ".mp3", ".wav", ".m4a", ".flac"}
_MIGRATION_VOICE_EXTS = {".ogg", ".opus"}


async def _standalone_send(
    pconfig,
    chat_id,
    message,
    *,
    thread_id=None,
    media_files=None,
    force_document=False,
):
    """Out-of-process Feishu/Lark delivery via the adapter's send pipeline.

    Implements the standalone_sender_fn contract so deliver=feishu cron jobs
    succeed when cron runs separately from the gateway. Builds a transient
    FeishuAdapter, hydrates its lark client, and sends text + native media
    (images, video, voice, documents). Replaces the legacy _send_feishu helper.
    """
    if not await asyncio.to_thread(_load_lark_oapi):
        return {"error": "Feishu dependencies not installed. Run `hermes setup` to install Feishu support."}

    media_files = media_files or []
    try:
        adapter = FeishuAdapter(pconfig)
        domain_name = getattr(adapter, "_domain_name", "feishu")
        domain = FEISHU_DOMAIN if domain_name != "lark" else LARK_DOMAIN
        adapter._client = adapter._build_lark_client(domain)
        metadata = {"thread_id": thread_id} if thread_id else None

        last_result = None
        if message.strip():
            last_result = await adapter.send(chat_id, message, metadata=metadata)
            if not last_result.success:
                return {"error": f"Feishu send failed: {last_result.error}"}

        for media_path, is_voice in media_files:
            if not os.path.exists(media_path):
                return {"error": f"Media file not found: {media_path}"}
            ext = os.path.splitext(media_path)[1].lower()
            if ext in _MIGRATION_IMAGE_EXTS:
                last_result = await adapter.send_image_file(chat_id, media_path, metadata=metadata)
            elif ext in _MIGRATION_VIDEO_EXTS:
                last_result = await adapter.send_video(chat_id, media_path, metadata=metadata)
            elif ext in _MIGRATION_VOICE_EXTS and is_voice:
                last_result = await adapter.send_voice(chat_id, media_path, metadata=metadata)
            elif ext in _MIGRATION_AUDIO_EXTS:
                last_result = await adapter.send_voice(chat_id, media_path, metadata=metadata)
            else:
                last_result = await adapter.send_document(chat_id, media_path, metadata=metadata)
            if not last_result.success:
                return {"error": f"Feishu media send failed: {last_result.error}"}

        if last_result is None:
            return {"error": "No deliverable text or media remained after processing MEDIA tags"}
        return {
            "success": True,
            "platform": "feishu",
            "chat_id": chat_id,
            "message_id": last_result.message_id,
        }
    except Exception as e:
        return {"error": f"Feishu send failed: {e}"}


def interactive_setup() -> None:
    """Interactive setup for Feishu / Lark — scan-to-create or manual creds.

    Replaces the central _setup_feishu in hermes_cli/gateway.py and the static
    _PLATFORMS["feishu"] dict. CLI helpers are lazy-imported.
    """
    from hermes_cli.config import get_env_value, remove_env_value, save_env_value
    from hermes_cli.setup import prompt_choice
    from hermes_cli.cli_output import (
        prompt,
        prompt_yes_no,
        print_header,
        print_info,
        print_success,
        print_warning,
        print_error,
    )

    print_header("Feishu / Lark")
    existing_app_id = get_env_value("FEISHU_APP_ID")
    existing_secret = get_env_value("FEISHU_APP_SECRET")
    if existing_app_id and existing_secret:
        print_success("Feishu / Lark is already configured.")
        if not prompt_yes_no("Reconfigure Feishu / Lark?", False):
            return

    method_idx = prompt_choice(
        "How would you like to set up Feishu / Lark?",
        [
            "Scan QR code to create a new bot automatically (recommended)",
            "Enter existing App ID and App Secret manually",
        ],
        0,
    )

    credentials = None
    used_qr = False

    if method_idx == 0:
        try:
            credentials = qr_register()
        except KeyboardInterrupt:
            print_warning("Feishu / Lark setup cancelled.")
            return
        except Exception as exc:
            print_warning(f"QR registration failed: {exc}")
        if credentials:
            used_qr = True
        else:
            print_info("QR setup did not complete. Continuing with manual input.")

    if not credentials:
        print_info("Go to https://open.feishu.cn/ (or https://open.larksuite.com/ for Lark)")
        print_info("Create an app, enable the Bot capability, and copy the credentials.")
        app_id = prompt("App ID", password=False)
        if not app_id:
            print_warning("Skipped — Feishu / Lark won't work without an App ID.")
            return
        app_secret = prompt("App Secret", password=True)
        if not app_secret:
            print_warning("Skipped — Feishu / Lark won't work without an App Secret.")
            return
        domain_idx = prompt_choice("Domain", ["feishu (China)", "lark (International)"], 0)
        domain = "lark" if domain_idx == 1 else "feishu"

        bot_name = None
        try:
            bot_info = probe_bot(app_id, app_secret, domain)
            if bot_info:
                bot_name = bot_info.get("bot_name")
                print_success(f"Credentials verified — bot: {bot_name or 'unnamed'}")
            else:
                print_warning("Could not verify bot connection. Credentials saved anyway.")
        except Exception as exc:
            print_warning(f"Credential verification skipped: {exc}")

        credentials = {
            "app_id": app_id,
            "app_secret": app_secret,
            "domain": domain,
            "open_id": None,
            "bot_name": bot_name,
        }

    app_id = credentials["app_id"]
    app_secret = credentials["app_secret"]
    domain = credentials.get("domain", "feishu")
    open_id = credentials.get("open_id")
    bot_name = credentials.get("bot_name")

    save_env_value("FEISHU_APP_ID", app_id)
    save_env_value("FEISHU_APP_SECRET", app_secret)
    save_env_value("FEISHU_DOMAIN", domain)

    if used_qr:
        connection_mode = "websocket"
    else:
        mode_idx = prompt_choice(
            "Connection mode",
            [
                "WebSocket (recommended — no public URL needed)",
                "Webhook (requires a reachable HTTP endpoint)",
            ],
            0,
        )
        connection_mode = "webhook" if mode_idx == 1 else "websocket"
        if connection_mode == "webhook":
            print_info("Webhook defaults: 127.0.0.1:8765/feishu/webhook")
            print_info("Override with FEISHU_WEBHOOK_HOST / FEISHU_WEBHOOK_PORT / FEISHU_WEBHOOK_PATH")
            print_info("For signature verification, set FEISHU_ENCRYPT_KEY and FEISHU_VERIFICATION_TOKEN")
    save_env_value("FEISHU_CONNECTION_MODE", connection_mode)

    if bot_name:
        print_success(f"Bot created: {bot_name}")

    access_idx = prompt_choice(
        "How should direct messages be authorized?",
        [
            "Use DM pairing approval (recommended)",
            "Allow all direct messages",
            "Only allow listed user IDs",
        ],
        0,
    )
    if access_idx == 0:
        save_env_value("FEISHU_ALLOW_ALL_USERS", "false")
        save_env_value("FEISHU_ALLOWED_USERS", "")
        print_success("DM pairing enabled.")
        print_info("Unknown users can request access; approve with `hermes pairing approve`.")
    elif access_idx == 1:
        save_env_value("FEISHU_ALLOW_ALL_USERS", "true")
        save_env_value("FEISHU_ALLOWED_USERS", "")
        print_warning("Open DM access enabled for Feishu / Lark.")
    else:
        save_env_value("FEISHU_ALLOW_ALL_USERS", "false")
        default_allow = open_id or ""
        allowlist = prompt(
            "Allowed user IDs (comma-separated)", default_allow, password=False
        ).replace(" ", "")
        save_env_value("FEISHU_ALLOWED_USERS", allowlist)
        print_success("Allowlist saved.")

    group_idx = prompt_choice(
        "How should group chats be handled?",
        [
            "Respond only when @mentioned in groups (recommended)",
            "Disable group chats",
        ],
        0,
    )
    if group_idx == 0:
        save_env_value("FEISHU_GROUP_POLICY", "open")
        print_info("Group chats enabled (bot must be @mentioned).")
    else:
        save_env_value("FEISHU_GROUP_POLICY", "disabled")
        print_info("Group chats disabled.")

    print_info(
        "Leave blank to clear a previously saved home channel "
        "(cron / notifications)."
    )
    home_channel = prompt("Home chat ID (optional, for cron/notifications)", password=False).strip()
    if home_channel:
        save_env_value("FEISHU_HOME_CHANNEL", home_channel)
        print_success(f"Home channel set to {home_channel}")
    else:
        if remove_env_value("FEISHU_HOME_CHANNEL"):
            print_info("Home channel cleared.")

    print_success("🪽 Feishu / Lark configured!")
    print_info(f"App ID: {app_id}")
    print_info(f"Domain: {domain}")
    if bot_name:
        print_info(f"Bot: {bot_name}")


def _apply_yaml_config(yaml_cfg: dict, feishu_cfg: dict) -> dict | None:
    """Translate config.yaml feishu: keys into FEISHU_* env vars.

    Implements the apply_yaml_config_fn contract (#24849). Mirrors the legacy
    feishu_cfg block from gateway/config.py::load_gateway_config() (allow_bots).
    Env vars take precedence over YAML. Returns None — flows through env.
    """
    if "allow_bots" in feishu_cfg and not os.getenv("FEISHU_ALLOW_BOTS"):
        os.environ["FEISHU_ALLOW_BOTS"] = str(feishu_cfg["allow_bots"]).lower()
    return None


def _is_connected(config) -> bool:
    """Feishu is connected when app_id is configured. Mirrors the legacy
    _PLATFORM_CONNECTED_CHECKERS[Platform.FEISHU] = lambda cfg: bool(app_id)."""
    extra = getattr(config, "extra", {}) or {}
    return bool(extra.get("app_id"))


def _build_adapter(config):
    """Factory wrapper that constructs FeishuAdapter from a PlatformConfig."""
    return FeishuAdapter(config)


def register(ctx) -> None:
    """Plugin entry point — called by the Hermes plugin system."""
    ctx.register_platform(
        name="feishu",
        label="Feishu / Lark",
        adapter_factory=_build_adapter,
        check_fn=feishu_deps_present,
        ensure_deps_fn=check_feishu_requirements,
        is_connected=_is_connected,
        validate_config=_is_connected,
        required_env=["FEISHU_APP_ID", "FEISHU_APP_SECRET"],
        install_hint="Run `hermes setup` to install Feishu support.",
        setup_fn=interactive_setup,
        apply_yaml_config_fn=_apply_yaml_config,
        allowed_users_env="FEISHU_ALLOWED_USERS",
        allow_all_env="FEISHU_ALLOW_ALL_USERS",
        cron_deliver_env_var="FEISHU_HOME_CHANNEL",
        standalone_sender_fn=_standalone_send,
        max_message_length=8000,
        emoji="🪽",
        allow_update_command=True,
    )
