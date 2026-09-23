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
import threading
import time
from collections import OrderedDict
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

#: How often a session pays for a complexity decision.
#:
#: ``session`` (the default) rates the first turn of a session and reuses that
#: band for the rest of it: one decision, one model, no mid-conversation model
#: switch — which is also what keeps the prompt cache intact. ``turn`` rates
#: every turn, which follows the work as it changes but re-decides (and can
#: re-route) on each message.
COMPLEXITY_SCOPES = ("session", "turn")
DEFAULT_COMPLEXITY_SCOPE = "session"

#: A session's decided band, so ``session`` scope classifies once. Bounded
#: rather than unbounded: a long-lived gateway sees many sessions, and this
#: holds only a short string per session.
_SESSION_BAND_MAX_ENTRIES = 512
_SESSION_BAND_TTL_SECONDS = 24 * 60 * 60.0

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


def _as_scope(raw: Any) -> str:
    """Parse the complexity scope, falling back loudly on an unknown value."""
    if raw is None:
        return DEFAULT_COMPLEXITY_SCOPE
    value = str(raw).strip().lower()
    if not value:
        return DEFAULT_COMPLEXITY_SCOPE
    if value in COMPLEXITY_SCOPES:
        return value
    logger.warning(
        "[Jev] unknown complexity scope %r (expected one of %s); using %r",
        raw,
        "/".join(COMPLEXITY_SCOPES),
        DEFAULT_COMPLEXITY_SCOPE,
    )
    return DEFAULT_COMPLEXITY_SCOPE


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
    complexity_scope: str = DEFAULT_COMPLEXITY_SCOPE
    business_scope: str = ""
    relevance_threshold: float = 0.7
    min_confidence: float = 0.5
    tier_models: Dict[str, TierModel] = field(default_factory=dict)

    @property
    def configured(self) -> bool:
        """Can the client reach Jev at all?"""
        return bool(self.api_key)


def _configured_tier(raw: Any) -> Tuple[str, Optional[str]]:
    """Read one ``jev.models.<band>`` entry as ``(model, provider)``.

    Accepts the shorthand ``"model-name"`` and the full
    ``{model: ..., provider: ...}`` form.
    """
    if isinstance(raw, str):
        return raw.strip(), None
    if isinstance(raw, dict):
        model = str(raw.get("model") or "").strip()
        provider = str(raw.get("provider") or "").strip() or None
        return model, provider
    return "", None


def _resolve_tier_models(config: Mapping[str, Any]) -> Dict[str, TierModel]:
    """Resolve each band's model and provider, env overriding config per FIELD.

    ``HERMES_JEV_MODEL_<BAND>`` and ``HERMES_JEV_PROVIDER_<BAND>`` each override
    only the field they name.  Setting just the model env var therefore keeps a
    provider configured in ``config.yaml`` instead of silently discarding it —
    the two surfaces express the same thing, so neither should erase the other.
    """
    raw_models = config.get("models")
    raw_models = raw_models if isinstance(raw_models, dict) else {}
    resolved: Dict[str, TierModel] = {}

    for tier in COMPLEXITY_TIERS:
        model, provider = _configured_tier(raw_models.get(tier))

        env_model = _env(f"HERMES_JEV_MODEL_{tier.upper()}")
        if env_model:
            model = env_model
        env_provider = _env(f"HERMES_JEV_PROVIDER_{tier.upper()}")
        if env_provider:
            provider = env_provider

        if model:
            resolved[tier] = TierModel(model=model, provider=provider)
        elif provider:
            logger.warning(
                "[Jev] band %r has a provider (%s) but no model; the band is "
                "ignored — set HERMES_JEV_MODEL_%s or jev.models.%s",
                tier,
                provider,
                tier.upper(),
                tier,
            )
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
        complexity_scope=_as_scope(
            pick("HERMES_JEV_COMPLEXITY_SCOPE", "complexity_scope")
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


class _SessionBandMemo:
    """Remembers the band a session was rated into, for ``session`` scope."""

    def __init__(self):
        self._entries: "OrderedDict[str, tuple[float, str]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, session_key: str) -> Optional[str]:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(session_key)
            if entry is None:
                return None
            expires_at, tier = entry
            if expires_at <= now:
                self._entries.pop(session_key, None)
                return None
            self._entries.move_to_end(session_key)
            return tier

    def put(self, session_key: str, tier: str) -> None:
        with self._lock:
            self._entries[session_key] = (
                time.monotonic() + _SESSION_BAND_TTL_SECONDS,
                tier,
            )
            self._entries.move_to_end(session_key)
            while len(self._entries) > _SESSION_BAND_MAX_ENTRIES:
                self._entries.popitem(last=False)

    def forget(self, session_key: str) -> None:
        with self._lock:
            self._entries.pop(session_key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_session_bands = _SessionBandMemo()


def forget_session_band(session_key: Optional[str]) -> None:
    """Drop a session's remembered band so the next turn re-rates it.

    Session identity already does this implicitly — a reset mints a new session
    id, which is a different memo key — so this is for callers that reuse an id
    across a deliberate restart.
    """
    if session_key:
        _session_bands.forget(str(session_key))


def route_model_for_request(
    text: str,
    *,
    settings: Optional[JevSettings] = None,
    client: Optional[JevClient] = None,
    session_key: Optional[str] = None,
) -> Optional[TierModel]:
    """Classify ``text`` and return the band's model, or ``None`` for default.

    The single entry point for complexity routing: it re-checks the feature gate
    itself, so callers only need one guarded call.

    Under ``complexity_scope: session`` (the default) the band is decided on the
    first turn that yields one and reused for the rest of the session, so a
    conversation runs on one model rather than switching under itself. An
    undecided turn is deliberately NOT remembered — a transient error or one
    low-confidence rating must not pin a whole session to the default model, so
    the next turn tries again.

    ``session_key`` should be the session **id**, not the routing key: a reset
    mints a new id, which is what makes a fresh conversation re-rate. Without
    one, ``session`` scope degrades to per-turn classification.
    """
    settings = settings if settings is not None else load_jev_settings()
    if not complexity_routing_active(settings):
        return None

    per_session = settings.complexity_scope == "session" and bool(session_key)
    memo_key = str(session_key) if per_session else ""

    if per_session:
        remembered = _session_bands.get(memo_key)
        if remembered is not None:
            model = resolve_tier_model(remembered, settings)
            logger.info(
                "[Jev] complexity: %s reused for this session (scope=session) -> %s",
                remembered,
                model.model if model else "default model",
            )
            return model

    tier = judge_complexity(text, settings=settings, client=client)
    if per_session and tier is not None:
        _session_bands.put(memo_key, tier)
    return resolve_tier_model(tier, settings)


def _provider_key(value: Optional[str]) -> str:
    """Reduce a provider identifier to a comparable key, or "" if it names none.

    Provider ids appear at two granularities: the full id a profile requests
    (``custom:glm``) and the namespace a runtime canonicalizes it to
    (``custom``).  A bare name is a third spelling of the same thing — ``glm``
    and ``custom:glm`` are one provider.  A lone ``custom`` identifies no
    particular provider, so it reduces to "" and never matches anything.
    """
    text = (value or "").strip().lower()
    if not text or text == "custom":
        return ""
    return text.split(":", 1)[1] if text.startswith("custom:") else text


def _provider_ids_match(configured: Optional[str], pinned: Optional[str]) -> bool:
    """Do these two identifiers name the same provider?"""
    key = _provider_key(configured)
    return bool(key) and key == _provider_key(pinned)


def _agent_runtime_snapshot(agent: Any) -> Dict[str, Any]:
    """The fields a band swap can change, as they stand right now."""
    return {
        "model": getattr(agent, "model", None),
        "provider": getattr(agent, "provider", None),
        "requested_provider": getattr(agent, "requested_provider", None),
        "api_key": getattr(agent, "api_key", None),
        "base_url": getattr(agent, "base_url", None),
        "api_mode": getattr(agent, "api_mode", None),
    }


def _resolve_band_runtime(tier_model: TierModel) -> Optional[Dict[str, Any]]:
    """Credentials for a band pinned to its own provider, or ``None``.

    ``target_model`` is passed so ``api_mode`` is derived for the model being
    switched TO — dual-wire providers route different models through different
    API surfaces.
    """
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider

        runtime = resolve_runtime_provider(
            requested=tier_model.provider, target_model=tier_model.model,
        )
    except Exception as exc:
        logger.warning(
            "[Jev] band model %s is pinned to provider %s but its credentials did "
            "not resolve (%s); keeping the current model",
            tier_model.model,
            tier_model.provider,
            exc,
        )
        return None
    return {
        "model": tier_model.model,
        # Keep ``provider`` canonical the way agent construction does, and carry
        # the full id separately so the next turn can recognise this provider.
        "provider": runtime.get("provider") or tier_model.provider,
        "requested_provider": tier_model.provider,
        "api_key": runtime.get("api_key"),
        "base_url": runtime.get("base_url"),
        "api_mode": runtime.get("api_mode"),
    }


def _switch_agent_runtime(agent: Any, target: Dict[str, Any]) -> bool:
    """Move a live agent onto ``target``. Returns False if it could not.

    ``AIAgent.switch_model`` is the supported in-place swap — it rebuilds the
    provider clients, refreshes the credential pool and caching flags, and
    restores the previous runtime atomically if the rebuild raises. On failure
    the agent keeps the runtime it already had.
    """
    switch = getattr(agent, "switch_model", None)
    if not callable(switch):
        logger.debug("[Jev] agent has no switch_model; cannot change provider")
        return False
    try:
        switch(
            new_model=target["model"],
            new_provider=target["provider"],
            api_key=target.get("api_key") or "",
            base_url=target.get("base_url") or "",
            api_mode=target.get("api_mode") or "",
        )
    except Exception as exc:
        logger.warning(
            "[Jev] could not switch to %s on %s (%s); the agent keeps its current runtime",
            target.get("model"),
            target.get("requested_provider") or target.get("provider"),
            exc,
        )
        return False
    # switch_model sets requested_provider to whatever it was handed; restore
    # the full id so a later band comparison still recognises this provider.
    try:
        agent.requested_provider = target.get("requested_provider") or target["provider"]
    except Exception:
        pass
    return True


def apply_tier_to_agent(agent: Any, tier_model: Optional[TierModel]) -> Optional[str]:
    """Point a long-lived agent at ``tier_model`` for the coming turn.

    Used by surfaces that reuse one cached ``AIAgent`` across turns (the
    WORKAGENT A2A executor). The agent's own runtime is captured the first time
    this runs and restored whenever a turn resolves to no band, so turning the
    feature off — or a single low-confidence turn — never leaves the agent
    stranded on the previous band.

    A band pinned to a *different provider* is honored: its credentials are
    resolved and the agent is swapped onto them in place. A band with no
    provider swaps only the model and keeps the session's own credentials.
    Returns the model now in effect, or ``None`` when the agent was untouched.
    """
    if agent is None:
        return None

    baseline = getattr(agent, "_jev_baseline_runtime", None)
    if baseline is None:
        baseline = _agent_runtime_snapshot(agent)
        try:
            agent._jev_baseline_runtime = baseline
        except Exception:
            return None

    # ── Decide the runtime this turn should run on ──
    target: Optional[Dict[str, Any]] = None
    if tier_model is not None and tier_model.model:
        if not tier_model.provider or _provider_ids_match(
            getattr(agent, "requested_provider", None) or getattr(agent, "provider", None),
            tier_model.provider,
        ):
            # Same provider: only the model moves.
            if getattr(agent, "model", None) != tier_model.model:
                try:
                    agent.model = tier_model.model
                except Exception:
                    logger.debug("[Jev] could not set agent.model", exc_info=True)
                    return None
                logger.info("[Jev] routing this turn to %s", tier_model.model)
            return tier_model.model
        target = _resolve_band_runtime(tier_model)
        if target is None:
            return getattr(agent, "model", None)
    else:
        # No band: go back to the runtime the profile gave this agent.
        snapshot = _agent_runtime_snapshot(agent)
        if snapshot == baseline:
            return baseline.get("model")
        if (
            snapshot.get("requested_provider") == baseline.get("requested_provider")
            and snapshot.get("base_url") == baseline.get("base_url")
        ):
            # Only the model drifted, so undo it the same cheap way it was
            # applied — a client rebuild here would be pure cost, and would
            # require switch_model on surfaces that never needed it.
            try:
                agent.model = baseline["model"]
            except Exception:
                logger.debug("[Jev] could not restore agent.model", exc_info=True)
                return None
            logger.info("[Jev] restoring the profile model %s", baseline["model"])
            return baseline["model"]
        target = dict(baseline)

    if target["model"] == getattr(agent, "model", None) and _provider_ids_match(
        getattr(agent, "requested_provider", None),
        target.get("requested_provider"),
    ):
        return target["model"]

    logger.info(
        "[Jev] switching this turn to %s on %s (was %s on %s)",
        target["model"],
        target.get("requested_provider") or target.get("provider"),
        getattr(agent, "model", None),
        getattr(agent, "requested_provider", None) or getattr(agent, "provider", None),
    )
    if not _switch_agent_runtime(agent, target):
        return getattr(agent, "model", None)
    return target["model"]
