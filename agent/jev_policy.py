"""Jev-backed admission and complexity policy for gateway / A2A turns.

Two independent, independently-gated decisions sit in front of the agent loop:

1. **Channel admission** — a group message that does not @-mention the bot is
   normally dropped.  With ``HERMES_JEV_CHANNEL_AUTOREPLY`` on, Jev is asked
   whether the message falls inside the agent's configured business scope; only
   then is the message admitted (and answered in a topic/thread under it).
2. **Complexity routing** — with ``HERMES_JEV_COMPLEXITY_ROUTING`` on, Jev rates
   the request low / medium / high and the turn is routed to the model
   configured for that band.  Unconfigured band → the agent's default model.

Both are OFF by default and both fail *safe*: with no API key, no configured
scope, no configured band model, a low-confidence answer, or any transport
error, the caller keeps the exact behavior it had before Jev existed.

Settings resolve env-var-first (the operator-facing interface), then
``config.yaml``'s ``jev:`` section, then the built-in default.  Secrets and
per-profile values go through ``agent.secret_scope`` so a multiplexing gateway
cannot read another profile's key or scope.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

from agent.jev_client import (
    DEFAULT_JEV_BASE_URL,
    DEFAULT_JEV_MODEL,
    DEFAULT_JEV_TIMEOUT,
    JevCallStats,
    JevClient,
    JevDecision,
    noul,
    score,
)

logger = logging.getLogger(__name__)

#: Ordered low → high. The band names are also the ``score`` criteria sent to
#: Jev, so renaming one changes the question — keep them stable.
COMPLEXITY_TIERS = ("low", "medium", "high")

# Admission is two separate propositions, not one.  Folding "is it our topic"
# and "does it want an answer" into a single question squashes both signals
# toward the middle and makes the threshold meaningless; asked apart they
# separate cleanly, and Jev answers both in one forward pass anyway.
_SCOPE_QUESTION = (
    "`business_scope` lists what the assistant is responsible for. `message` was "
    "posted in a group chat the assistant is a member of. Is `message` about a "
    "topic covered by `business_scope`?"
)

_WANTS_ANSWER_QUESTION = (
    "`message` was posted in a group chat. Is it asking for help, information, or "
    "action from whoever can provide it? Answer false for statements, "
    "acknowledgements, status updates, small talk, and messages clearly directed "
    "at a specific named person."
)

_COMPLEXITY_QUESTION = (
    "How much reasoning depth and how many steps does it take to fully answer "
    "`request`? low = a greeting, lookup, or single short factual answer. "
    "medium = a focused task needing a few steps, a tool call, or a short piece "
    "of code. high = multi-step work needing planning, deep analysis, "
    "cross-referencing, or a substantial code change."
)


# ---------------------------------------------------------------------------
# Env / config resolution
# ---------------------------------------------------------------------------


def _env(name: str) -> Optional[str]:
    """Read a profile-scoped env var, or ``None`` when it cannot be resolved.

    Under a multiplexing gateway an unscoped read raises rather than leaking
    another profile's value; treat that as "unset", which turns the feature off.
    """
    try:
        from agent.secret_scope import UnscopedSecretError, get_secret

        try:
            raw = get_secret(name)
        except UnscopedSecretError:
            logger.debug("[Jev] %s unavailable outside a profile secret scope", name)
            return None
    except Exception:
        import os

        raw = os.getenv(name)
    if raw is None:
        return None
    raw = str(raw).strip()
    return raw or None


def _jev_config() -> Mapping[str, Any]:
    try:
        from hermes_cli.config import load_config_readonly

        section = load_config_readonly().get("jev")
    except Exception:
        return {}
    return section if isinstance(section, dict) else {}


def _as_bool(raw: Any, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    lowered = str(raw).strip().lower()
    if lowered in {"true", "1", "yes", "on"}:
        return True
    if lowered in {"false", "0", "no", "off"}:
        return False
    return default


def _as_float(raw: Any, default: float) -> float:
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class TierModel:
    """The model a complexity band routes to."""

    model: str
    provider: Optional[str] = None


@dataclass(frozen=True)
class JevSettings:
    api_key: str = ""
    base_url: str = DEFAULT_JEV_BASE_URL
    model: str = DEFAULT_JEV_MODEL
    timeout: float = DEFAULT_JEV_TIMEOUT
    channel_autoreply: bool = False
    complexity_routing: bool = False
    business_scope: str = ""
    relevance_threshold: float = 0.7
    min_confidence: float = 0.5
    tier_models: Dict[str, TierModel] = field(default_factory=dict)

    @property
    def configured(self) -> bool:
        """Can the client reach Jev at all?"""
        return bool(self.api_key)


def _resolve_tier_models(config: Mapping[str, Any]) -> Dict[str, TierModel]:
    raw_models = config.get("models")
    raw_models = raw_models if isinstance(raw_models, dict) else {}
    resolved: Dict[str, TierModel] = {}
    for tier in COMPLEXITY_TIERS:
        env_value = _env(f"HERMES_JEV_MODEL_{tier.upper()}")
        if env_value:
            resolved[tier] = TierModel(model=env_value)
            continue
        configured = raw_models.get(tier)
        if isinstance(configured, str) and configured.strip():
            resolved[tier] = TierModel(model=configured.strip())
        elif isinstance(configured, dict):
            model = str(configured.get("model") or "").strip()
            if model:
                provider = str(configured.get("provider") or "").strip() or None
                resolved[tier] = TierModel(model=model, provider=provider)
    return resolved


def load_jev_settings() -> JevSettings:
    """Resolve the effective settings: env var → ``config.yaml`` → default."""
    config = _jev_config()

    def pick(env_name: str, config_key: str) -> Optional[Any]:
        value = _env(env_name)
        if value is not None:
            return value
        return config.get(config_key)

    return JevSettings(
        api_key=_env("TYPESAFE_API_KEY") or "",
        base_url=str(pick("TYPESAFE_BASE_URL", "base_url") or DEFAULT_JEV_BASE_URL),
        model=str(pick("TYPESAFE_MODEL", "model") or DEFAULT_JEV_MODEL),
        timeout=_as_float(pick("HERMES_JEV_TIMEOUT", "timeout"), DEFAULT_JEV_TIMEOUT),
        channel_autoreply=_as_bool(
            pick("HERMES_JEV_CHANNEL_AUTOREPLY", "channel_autoreply"), False
        ),
        complexity_routing=_as_bool(
            pick("HERMES_JEV_COMPLEXITY_ROUTING", "complexity_routing"), False
        ),
        business_scope=str(pick("HERMES_JEV_BUSINESS_SCOPE", "business_scope") or "").strip(),
        relevance_threshold=_as_float(
            pick("HERMES_JEV_RELEVANCE_THRESHOLD", "relevance_threshold"), 0.7
        ),
        min_confidence=_as_float(pick("HERMES_JEV_MIN_CONFIDENCE", "min_confidence"), 0.5),
        tier_models=_resolve_tier_models(config),
    )


def build_client(settings: Optional[JevSettings] = None) -> Optional[JevClient]:
    """A client for these settings, or ``None`` when Jev is not configured."""
    settings = settings if settings is not None else load_jev_settings()
    if not settings.configured:
        return None
    return JevClient(
        api_key=settings.api_key,
        base_url=settings.base_url,
        model=settings.model,
        timeout=settings.timeout,
    )


# ---------------------------------------------------------------------------
# Feature gates
# ---------------------------------------------------------------------------


def channel_autoreply_active(settings: Optional[JevSettings] = None) -> bool:
    """Is unprompted in-channel answering fully configured and switched on?

    Requires the flag, a reachable Jev, and a business scope — without a scope
    there is nothing to judge relevance against, so the gate stays closed and
    non-mention messages keep being dropped.
    """
    settings = settings if settings is not None else load_jev_settings()
    if not settings.channel_autoreply:
        return False
    if not settings.configured:
        logger.debug("[Jev] channel autoreply requested but TYPESAFE_API_KEY is unset")
        return False
    if not settings.business_scope:
        logger.warning(
            "[Jev] channel autoreply is on but HERMES_JEV_BUSINESS_SCOPE / jev.business_scope "
            "is empty — keeping the mention gate closed"
        )
        return False
    return True


def complexity_routing_active(settings: Optional[JevSettings] = None) -> bool:
    """Is complexity-based model routing configured and switched on?"""
    settings = settings if settings is not None else load_jev_settings()
    if not settings.complexity_routing:
        return False
    if not settings.configured:
        logger.debug("[Jev] complexity routing requested but TYPESAFE_API_KEY is unset")
        return False
    if not settings.tier_models:
        logger.debug(
            "[Jev] complexity routing is on but no HERMES_JEV_MODEL_{LOW,MEDIUM,HIGH} "
            "is configured — every band keeps the agent's default model"
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def _ask(
    client: Any,
    state: Mapping[str, Any],
    questions: Mapping[str, Mapping[str, Any]],
) -> Tuple[Optional[Dict[str, JevDecision]], JevCallStats]:
    """Ask ``client`` and always come back with call stats to log.

    ``client`` is an injection point (tests and embedders pass their own), so a
    client implementing only the minimal ``ask`` contract is timed here rather
    than being required to report its own stats.
    """
    detailed = getattr(client, "ask_detailed", None)
    if callable(detailed):
        return detailed(state, questions)

    started = time.monotonic()
    answers = client.ask(state, questions)
    return answers, JevCallStats(
        elapsed_ms=(time.monotonic() - started) * 1000.0,
        asked=len(questions),
        from_cache=0,
        over_wire=len(questions),
    )


def _relevance_state(
    text: str,
    *,
    business_scope: str,
    channel_name: str = "",
    sender_name: str = "",
) -> Dict[str, str]:
    state = {"business_scope": business_scope, "message": text}
    if channel_name:
        state["channel"] = channel_name
    if sender_name:
        state["sender"] = sender_name
    return state


def judge_channel_relevance(
    text: str,
    *,
    settings: JevSettings,
    client: Optional[JevClient] = None,
    channel_name: str = "",
    sender_name: str = "",
) -> Optional[bool]:
    """Should this unmentioned channel message be answered?

    ``True``/``False`` is a decision; ``None`` means Jev could not decide and
    the caller must keep its pre-Jev behavior (drop the message).
    """
    if not (text or "").strip():
        logger.debug("[Jev] channel admission skipped: empty message")
        return None
    client = client if client is not None else build_client(settings)
    if client is None:
        logger.debug("[Jev] channel admission skipped: no client (TYPESAFE_API_KEY unset)")
        return None

    answers, stats = _ask(
        client,
        _relevance_state(
            text,
            business_scope=settings.business_scope,
            channel_name=channel_name,
            sender_name=sender_name,
        ),
        {
            "in_scope": noul(_SCOPE_QUESTION),
            "wants_answer": noul(_WANTS_ANSWER_QUESTION),
        },
    )
    in_scope = (answers or {}).get("in_scope")
    wants_answer = (answers or {}).get("wants_answer")
    if in_scope is None or wants_answer is None:
        logger.warning(
            "[Jev] channel admission: UNDECIDED in %.0fms (%s) — %s; caller keeps its default",
            stats.elapsed_ms,
            stats.source,
            stats.error or "answer missing from response",
        )
        return None
    admit = (
        in_scope.probability >= settings.relevance_threshold
        and wants_answer.probability >= settings.relevance_threshold
    )
    logger.info(
        "[Jev] channel admission: %s in %.0fms (%s) — in_scope=%.2f wants_answer=%.2f "
        "threshold=%.2f chat=%s",
        "ANSWER" if admit else "STAY QUIET",
        stats.elapsed_ms,
        stats.source,
        in_scope.probability,
        wants_answer.probability,
        settings.relevance_threshold,
        channel_name or "-",
    )
    return admit


async def judge_channel_relevance_async(
    text: str,
    *,
    settings: JevSettings,
    client: Optional[JevClient] = None,
    channel_name: str = "",
    sender_name: str = "",
) -> Optional[bool]:
    """``judge_channel_relevance`` off the event loop."""
    import asyncio

    return await asyncio.to_thread(
        judge_channel_relevance,
        text,
        settings=settings,
        client=client,
        channel_name=channel_name,
        sender_name=sender_name,
    )


def judge_complexity(
    text: str,
    *,
    settings: JevSettings,
    client: Optional[JevClient] = None,
) -> Optional[str]:
    """Rate the request ``low`` / ``medium`` / ``high``.

    ``None`` when Jev could not answer, or answered below
    ``min_confidence`` — an uncalibrated guess must not move a turn onto a
    different model, so the caller keeps its default.
    """
    if not (text or "").strip():
        logger.debug("[Jev] complexity skipped: empty request")
        return None
    client = client if client is not None else build_client(settings)
    if client is None:
        logger.debug("[Jev] complexity skipped: no client (TYPESAFE_API_KEY unset)")
        return None

    answers, stats = _ask(
        client,
        {"request": text},
        {"complexity": score(_COMPLEXITY_QUESTION, COMPLEXITY_TIERS)},
    )
    decision = (answers or {}).get("complexity")
    if decision is None or decision.label not in COMPLEXITY_TIERS:
        logger.warning(
            "[Jev] complexity: UNDECIDED in %.0fms (%s) — %s; keeping the default model",
            stats.elapsed_ms,
            stats.source,
            stats.error or "no usable band in response",
        )
        return None
    if decision.confidence < settings.min_confidence:
        logger.info(
            "[Jev] complexity: %s REJECTED in %.0fms (%s) — confidence=%.2f < %.2f, "
            "keeping the default model",
            decision.label,
            stats.elapsed_ms,
            stats.source,
            decision.confidence,
            settings.min_confidence,
        )
        return None
    logger.info(
        "[Jev] complexity: %s in %.0fms (%s) — confidence=%.2f >= %.2f",
        decision.label,
        stats.elapsed_ms,
        stats.source,
        decision.confidence,
        settings.min_confidence,
    )
    return decision.label


async def judge_complexity_async(
    text: str,
    *,
    settings: JevSettings,
    client: Optional[JevClient] = None,
) -> Optional[str]:
    """``judge_complexity`` off the event loop."""
    import asyncio

    return await asyncio.to_thread(
        judge_complexity, text, settings=settings, client=client
    )


def resolve_tier_model(
    tier: Optional[str], settings: Optional[JevSettings] = None
) -> Optional[TierModel]:
    """The model configured for ``tier``, or ``None`` to keep the default."""
    if not tier:
        return None
    settings = settings if settings is not None else load_jev_settings()
    return settings.tier_models.get(tier)


def route_model_for_request(
    text: str,
    *,
    settings: Optional[JevSettings] = None,
    client: Optional[JevClient] = None,
) -> Optional[TierModel]:
    """Classify ``text`` and return the band's model, or ``None`` for default.

    The single entry point for complexity routing: it re-checks the feature
    gate itself, so callers only need one guarded call.
    """
    settings = settings if settings is not None else load_jev_settings()
    if not complexity_routing_active(settings):
        return None
    tier = judge_complexity(text, settings=settings, client=client)
    return resolve_tier_model(tier, settings)


def apply_tier_to_agent(agent: Any, tier_model: Optional[TierModel]) -> Optional[str]:
    """Point a long-lived agent at ``tier_model`` for the coming turn.

    Used by surfaces that reuse one cached ``AIAgent`` across turns (the
    WORKAGENT A2A executor).  The agent's own model is captured the first time
    this runs and restored whenever a turn resolves to no band, so turning the
    feature off — or a single low-confidence turn — never leaves the agent
    stranded on the previous band's model.

    Only the model is swapped: credentials, ``base_url`` and ``api_mode`` stay
    as the profile resolved them.  A band pinned to a *different provider* is
    therefore skipped here (the gateway, which builds a fresh agent per route,
    is the surface that can honor one).  Returns the model now in effect, or
    ``None`` when the agent was left untouched.
    """
    if agent is None:
        return None
    baseline = getattr(agent, "_jev_baseline_model", None)
    if baseline is None:
        baseline = getattr(agent, "model", None)
        try:
            agent._jev_baseline_model = baseline
        except Exception:
            return None

    target = baseline
    if tier_model is not None and tier_model.model:
        agent_provider = str(getattr(agent, "provider", "") or "").strip().lower()
        pinned = (tier_model.provider or "").strip().lower()
        if pinned and pinned != agent_provider:
            logger.warning(
                "[Jev] band model %s is pinned to provider %s but this surface cannot "
                "switch providers mid-session; keeping %s",
                tier_model.model,
                tier_model.provider,
                baseline,
            )
        else:
            target = tier_model.model

    if target and getattr(agent, "model", None) != target:
        logger.info("[Jev] routing this turn to %s (was %s)", target, getattr(agent, "model", None))
        try:
            agent.model = target
        except Exception:
            logger.debug("[Jev] could not set agent.model", exc_info=True)
            return None
    return target
