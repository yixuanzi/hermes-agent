"""``platforms.lark_user`` YAML -> typed settings.

Every key lives in config.yaml (``PlatformConfig.extra``); the only env names are the AUTHZ
allow-all/allowlist pair in ``gateway_env.py``. ``load_settings`` raises ``SettingsError`` for a
configuration the adapter must refuse to run with (an empty reply prefix, an unknown enum value).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

DM_SCOPES = ("per_chat", "per_message")
GROUP_SCOPES = ("per_chat", "per_chat_user", "per_thread", "per_message")
DELIVERIES = ("review", "auto")
FORMATS = ("text", "post", "card")
TARGETS = ("reply", "thread", "send")
CONTEXT_SCOPES = ("recent", "thread")
CONTEXT_WHEN = ("new_session", "always")
POLICIES = ("allowlist", "open", "disabled")

_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h|d)?\s*$")
_DURATION_UNITS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, None: 1}
_HOURS_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


class SettingsError(ValueError):
    """A ``platforms.lark_user`` value the adapter refuses to start with."""


def parse_duration(value: Any, default: float) -> float:
    """``15``, ``"15s"``, ``"10m"``, ``"24h"`` -> seconds; None/"" -> ``default``."""
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    match = _DURATION_RE.match(str(value))
    if not match:
        raise SettingsError(f"invalid duration {value!r}")
    return float(match.group(1)) * _DURATION_UNITS[match.group(2)]


def parse_active_hours(value: Any) -> Optional[tuple[int, int]]:
    """``"09:00-22:00"`` -> (start_minute, end_minute); an end before the start wraps midnight."""
    if not value:
        return None
    match = _HOURS_RE.match(str(value))
    if not match:
        raise SettingsError(f"invalid active_hours {value!r} (expected HH:MM-HH:MM)")
    h1, m1, h2, m2 = (int(g) for g in match.groups())
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
        raise SettingsError(f"invalid active_hours {value!r}")
    return h1 * 60 + m1, h2 * 60 + m2


def _choice(value: Any, allowed: tuple[str, ...], default: str, key: str) -> str:
    text = str(value).strip().lower() if value not in (None, "") else default
    if text not in allowed:
        raise SettingsError(f"{key} must be one of {', '.join(allowed)} (got {value!r})")
    return text


def _bool(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _ids(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    items = value.split(",") if isinstance(value, str) else value
    return tuple(str(item).strip() for item in items if str(item).strip())


def _section(raw: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = raw.get(key)
    return value if isinstance(value, Mapping) else {}


@dataclass(frozen=True)
class SourceSettings:
    enabled: bool
    interval: float


@dataclass(frozen=True)
class PollSettings:
    interval: float = 15.0
    idle_interval: float = 60.0
    idle_after: int = 10
    jitter: float = 0.2
    active_hours: Optional[tuple[int, int]] = None
    max_backoff: float = 600.0
    lookback: float = 120.0
    dm: SourceSettings = SourceSettings(True, 15.0)
    mention: SourceSettings = SourceSettings(True, 15.0)
    include_at_all: bool = False


@dataclass(frozen=True)
class ContextSettings:
    """``context``: recent group messages handed to the agent with an @mention."""
    enabled: bool = False
    scope: str = "thread"                 # recent: the chat only; thread: plus the mention's topic
    limit: int = 20
    hours: float = 24.0
    thread_limit: int = 20
    msg_max_chars: int = 1000
    when: str = "new_session"             # new_session | always


@dataclass(frozen=True)
class ReplySettings:
    delivery: str = "review"
    format: str = "post"
    target: str = "reply"
    at_sender: bool = False
    prefix: str = "[🤖 AI 代答] "
    suffix: str = ""
    card_title: str = "🤖 {user_name} 的助理"


@dataclass(frozen=True)
class ChatSettings:
    """Effective settings for one chat: the global block with ``chats.<chat_id>`` laid over it."""
    enabled: bool = True
    dm_scope: str = "per_chat"
    group_scope: str = "per_chat_user"
    reply: ReplySettings = ReplySettings()
    context: ContextSettings = ContextSettings()


@dataclass(frozen=True)
class LarkUserSettings:
    cli_bin: str = "lark-cli"
    cli_profile: str = ""
    cli_timeout: float = 60.0
    poll: PollSettings = PollSettings()
    max_message_age: float = 600.0
    allow_bots: bool = False
    allow_external: bool = False
    rate_limit_per_chat: int = 5
    review_chat: str = ""
    review_ttl: float = 86400.0
    dm_policy: str = "allowlist"
    group_policy: str = "allowlist"
    allow_from: tuple[str, ...] = ()
    group_allow_from: tuple[str, ...] = ()
    defaults: ChatSettings = ChatSettings()
    chats: Mapping[str, ChatSettings] = field(default_factory=dict)

    def for_chat(self, chat_id: str) -> ChatSettings:
        return self.chats.get(chat_id, self.defaults)

    @property
    def uses_review(self) -> bool:
        return self.defaults.reply.delivery == "review" or any(
            c.reply.delivery == "review" for c in self.chats.values())


def _int(value: Any, default: int, key: str, minimum: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise SettingsError(f"{key} must be an integer (got {value!r})") from None
    if number < minimum:
        raise SettingsError(f"{key} must be >= {minimum} (got {number})")
    return number


def _hours(value: Any, default: float, key: str) -> float:
    if value is None or value == "":
        return default
    try:
        hours = float(value)
    except (TypeError, ValueError):
        raise SettingsError(f"{key} must be a number of hours (got {value!r})") from None
    if hours <= 0:
        raise SettingsError(f"{key} must be positive (got {hours})")
    return hours


def _context(raw: Mapping[str, Any], base: ContextSettings, where: str) -> ContextSettings:
    return ContextSettings(
        enabled=_bool(raw.get("enabled"), base.enabled),
        scope=_choice(raw.get("scope"), CONTEXT_SCOPES, base.scope, f"{where}context.scope"),
        limit=min(50, _int(raw.get("limit"), base.limit, f"{where}context.limit", 1)),
        hours=_hours(raw.get("hours"), base.hours, f"{where}context.hours"),
        thread_limit=min(50, _int(raw.get("thread_limit"), base.thread_limit, f"{where}context.thread_limit")),
        msg_max_chars=_int(raw.get("msg_max_chars"), base.msg_max_chars, f"{where}context.msg_max_chars"),
        when=_choice(raw.get("when"), CONTEXT_WHEN, base.when, f"{where}context.when"),
    )


def _reply(raw: Mapping[str, Any], base: ReplySettings, where: str) -> ReplySettings:
    prefix = raw.get("prefix", base.prefix)
    if not str(prefix or "").strip():
        raise SettingsError(f"{where}reply.prefix must not be empty: replies are sent under your own name")
    return ReplySettings(
        delivery=_choice(raw.get("delivery"), DELIVERIES, base.delivery, f"{where}reply.delivery"),
        format=_choice(raw.get("format"), FORMATS, base.format, f"{where}reply.format"),
        target=_choice(raw.get("target"), TARGETS, base.target, f"{where}reply.target"),
        at_sender=_bool(raw.get("at_sender"), base.at_sender),
        prefix=str(prefix),
        suffix=str(raw.get("suffix", base.suffix) or ""),
        card_title=str(_section(raw, "card").get("title") or base.card_title),
    )


def _chat(raw: Mapping[str, Any], base: ChatSettings, where: str) -> ChatSettings:
    session = _section(raw, "session")
    return ChatSettings(
        enabled=_bool(raw.get("enabled"), base.enabled),
        dm_scope=_choice(raw.get("dm_scope", session.get("dm_scope")), DM_SCOPES, base.dm_scope, f"{where}dm_scope"),
        group_scope=_choice(raw.get("group_scope", session.get("group_scope")), GROUP_SCOPES, base.group_scope,
                            f"{where}group_scope"),
        reply=_reply(_section(raw, "reply"), base.reply, where),
        context=_context(_section(raw, "context"), base.context, where),
    )


def _poll(raw: Mapping[str, Any]) -> PollSettings:
    interval = parse_duration(raw.get("interval"), 15.0)
    if interval < 5:
        raise SettingsError("poll.interval must be at least 5s (each search costs several API calls)")
    sources = _section(raw, "sources")

    def source(name: str) -> SourceSettings:
        cfg = _section(sources, name)
        return SourceSettings(_bool(cfg.get("enabled"), True), max(5.0, parse_duration(cfg.get("interval"), interval)))

    mention = _section(sources, "mention")
    return PollSettings(
        interval=interval,
        idle_interval=max(interval, parse_duration(raw.get("idle_interval"), 60.0)),
        idle_after=int(raw.get("idle_after", 10)),
        jitter=min(0.5, max(0.0, float(raw.get("jitter", 0.2)))),
        active_hours=parse_active_hours(raw.get("active_hours")),
        max_backoff=parse_duration(raw.get("max_backoff"), 600.0),
        # The CLI renders create_time to the minute and search indexing lags ~15s: never look back
        # less than two minutes or a message straddling a minute boundary is missed.
        lookback=max(120.0, 2 * interval, parse_duration(raw.get("lookback"), 0.0)),
        dm=source("dm"),
        mention=source("mention"),
        include_at_all=_bool(mention.get("include_at_all"), False),
    )


def load_settings(extra: Mapping[str, Any]) -> LarkUserSettings:
    raw = extra or {}
    defaults = _chat(raw, ChatSettings(), "")
    chats = {
        str(chat_id): _chat(cfg, defaults, f"chats.{chat_id}.")
        for chat_id, cfg in _section(raw, "chats").items() if isinstance(cfg, Mapping)
    }
    review = _section(raw, "review")
    rate = _section(raw, "rate_limit")
    return LarkUserSettings(
        cli_bin=str(raw.get("cli_bin") or "lark-cli"),
        cli_profile=str(raw.get("lark_cli_profile") or ""),
        cli_timeout=parse_duration(raw.get("cli_timeout"), 60.0),
        poll=_poll(_section(raw, "poll")),
        max_message_age=parse_duration(raw.get("max_message_age"), 600.0),
        allow_bots=_bool(raw.get("allow_bots"), False),
        allow_external=_bool(raw.get("allow_external"), False),
        rate_limit_per_chat=max(0, int(rate.get("per_chat", 5))),
        review_chat=str(review.get("chat") or ""),
        review_ttl=parse_duration(review.get("ttl"), 86400.0),
        dm_policy=_choice(raw.get("dm_policy"), POLICIES, "allowlist", "dm_policy"),
        group_policy=_choice(raw.get("group_policy"), POLICIES, "allowlist", "group_policy"),
        allow_from=_ids(raw.get("allow_from")),
        group_allow_from=_ids(raw.get("group_allow_from")),
        defaults=defaults,
        chats=chats,
    )
