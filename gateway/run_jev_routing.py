"""Jev complexity routing for gateway turns (fork).

``apply_jev_complexity_route`` re-points a turn route at the model, provider and reasoning effort
configured for the request's difficulty band (``agent.jev_policy``). It runs inside
``_resolve_turn_agent_config`` before the fast-mode tier overrides, so those follow the band model.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# The route's runtime fields a band pinned to another provider replaces wholesale.
_PROVIDER_RUNTIME_KEYS = (
    "api_key", "base_url", "provider", "requested_provider", "api_mode", "command", "args",
    "credential_pool", "max_tokens", "capabilities",
)


def session_model_pinned(runner: Any, session_key: Optional[str]) -> bool:
    """True when the session chose its model with ``/model`` — a classifier never overrules that."""
    if not session_key:
        return False
    peek = getattr(runner, "_peek_session_state", None)
    state = peek(session_key) if callable(peek) else None
    return bool(state is not None and state.conversation.model_override)


def _switch_route_provider(route: dict, tier_model: Any) -> Optional[dict]:
    """Swap the route onto the band's provider; the band provider's request overrides, or None."""
    from gateway.run import _resolve_runtime_agent_kwargs_for_provider

    try:
        provider_runtime = _resolve_runtime_agent_kwargs_for_provider(
            tier_model.provider, target_model=tier_model.model)
    except Exception as exc:
        logger.warning("Jev band model %s pinned to provider %s but credentials did not resolve (%s); "
                       "keeping the session model", tier_model.model, tier_model.provider, exc)
        return None
    runtime = route["runtime"]
    for key in _PROVIDER_RUNTIME_KEYS:
        runtime[key] = provider_runtime.get(key)
    runtime["args"] = list(runtime.get("args") or [])
    runtime["capabilities"] = dict(runtime.get("capabilities") or {})
    return dict(provider_runtime.get("request_overrides") or {})


def _rederive_api_mode(route: dict, tier_model: Any) -> None:
    """Same provider, other model: api_mode can be model-derived (OpenCode Zen/Go)."""
    runtime = route["runtime"]
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider

        resolved = resolve_runtime_provider(
            requested=runtime.get("requested_provider") or runtime.get("provider"), target_model=tier_model.model)
        if resolved.get("api_mode"):
            runtime["api_mode"] = resolved.get("api_mode")
    except Exception:
        logger.debug("Could not re-derive api_mode for Jev band model %s; keeping %s",
                     tier_model.model, runtime.get("api_mode"), exc_info=True)


def _decided_tier(user_message: str, session_id: Optional[str]) -> Any:
    """The configured band for this request, or None (routing off, undecided, or any failure)."""
    try:
        from agent import jev_policy

        settings = jev_policy.load_jev_settings()
        if not jev_policy.complexity_routing_active(settings):
            return None
        return jev_policy.route_model_for_request(user_message, settings=settings, session_key=session_id)
    except Exception:
        logger.warning("Jev complexity routing failed; keeping the session model", exc_info=True)
        return None


def apply_jev_complexity_route(
    user_message: str, route: dict, session_id: Optional[str] = None, *,
    model_pinned: bool = False, request_overrides: Optional[dict] = None,
) -> Optional[dict]:
    """Re-point ``route`` at this turn's Jev band; return the request overrides the route now uses.

    Off unless complexity routing is configured and the session has not pinned its model with
    ``/model``. Mutates ``route`` in place, ``signature`` included: the agent-cache key derives
    from it, so a band switch rebuilds the agent — the same boundary ``/model`` crosses, which is
    why the default ``complexity_scope: session`` decides once per session id. A band's effort is
    handed back as ``route["band_reasoning_config"]`` (also when its model is unreachable) for the
    caller to rank below a session ``/reasoning`` override; it never enters ``signature``.
    """
    if model_pinned:
        logger.debug("Jev complexity routing skipped: session model pinned by /model (%s)", route.get("model"))
        return request_overrides
    if not (user_message or "").strip():
        return request_overrides
    tier_model = _decided_tier(user_message, session_id)
    if tier_model is None:
        return request_overrides
    if tier_model.reasoning_config is not None:
        route["band_reasoning_config"] = tier_model.reasoning_config
    if not tier_model.model or (tier_model.model == route["model"] and not tier_model.provider):
        if tier_model.reasoning_config is not None:
            logger.info("Jev complexity routing: keeping %s, reasoning_effort=%s",
                        route["model"] or "(default)", tier_model.reasoning_effort)
        return request_overrides
    if tier_model.provider:
        band_overrides = _switch_route_provider(route, tier_model)
        if band_overrides is None:
            return request_overrides
        request_overrides = band_overrides
    else:
        _rederive_api_mode(route, tier_model)
    from agent import jev_policy

    logger.info("Jev complexity routing: %s -> %s", route["model"] or "(default)", jev_policy.describe_tier(tier_model))
    runtime = route["runtime"]
    route["model"] = tier_model.model
    route["signature"] = (
        tier_model.model, runtime["provider"], runtime["requested_provider"], runtime["base_url"],
        runtime["api_mode"], runtime["command"], tuple(runtime["args"]),
    )
    return request_overrides
