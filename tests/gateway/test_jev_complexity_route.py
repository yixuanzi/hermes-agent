"""Gateway turn routing by Jev-rated task complexity.

``_apply_jev_complexity_route`` mutates the turn's route dict in place. Its
signature feeds the agent-cache key, so a band switch has to move both the
model and the signature, and every failure mode has to leave the route alone.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.jev_policy import TierModel
from gateway.run import _apply_jev_complexity_route


def _route(model="session-model", provider="openai", api_mode="chat"):
    runtime = {
        "api_key": "sk-session",
        "base_url": "https://session.test",
        "provider": provider,
        "requested_provider": provider,
        "api_mode": api_mode,
        "command": None,
        "args": [],
        "credential_pool": None,
        "max_tokens": None,
    }
    return {
        "model": model,
        "runtime": runtime,
        "signature": (
            model, provider, provider, runtime["base_url"], api_mode, None, (),
        ),
    }


def _stub_policy(monkeypatch, *, active=True, tier_model=None, raises=False):
    from agent import jev_policy

    monkeypatch.setattr(jev_policy, "load_jev_settings", lambda: SimpleNamespace(name="stub"))
    monkeypatch.setattr(jev_policy, "complexity_routing_active", lambda _s: active)

    def _route_model(_text, settings=None, client=None):
        if raises:
            raise RuntimeError("classifier exploded")
        return tier_model

    monkeypatch.setattr(jev_policy, "route_model_for_request", _route_model)


def test_the_band_model_replaces_the_session_model(monkeypatch):
    _stub_policy(monkeypatch, tier_model=TierModel(model="big-model"))
    route = _route()
    _apply_jev_complexity_route("rebuild the auth layer", route)
    assert route["model"] == "big-model"


def test_the_signature_follows_the_model_so_the_agent_cache_rebuilds(monkeypatch):
    _stub_policy(monkeypatch, tier_model=TierModel(model="big-model"))
    route = _route()
    before = route["signature"]
    _apply_jev_complexity_route("rebuild the auth layer", route)
    assert route["signature"] != before
    assert route["signature"][0] == "big-model"


def test_credentials_are_untouched_when_the_band_stays_on_the_same_provider(monkeypatch):
    _stub_policy(monkeypatch, tier_model=TierModel(model="big-model"))
    route = _route()
    _apply_jev_complexity_route("…", route)
    assert route["runtime"]["api_key"] == "sk-session"
    assert route["runtime"]["base_url"] == "https://session.test"
    assert route["runtime"]["provider"] == "openai"


def test_routing_off_leaves_the_route_alone(monkeypatch):
    _stub_policy(monkeypatch, active=False, tier_model=TierModel(model="big-model"))
    route = _route()
    _apply_jev_complexity_route("…", route)
    assert route["model"] == "session-model"


def test_no_band_leaves_the_route_alone(monkeypatch):
    _stub_policy(monkeypatch, tier_model=None)
    route = _route()
    _apply_jev_complexity_route("…", route)
    assert route["model"] == "session-model"


def test_a_classifier_failure_leaves_the_route_alone(monkeypatch):
    _stub_policy(monkeypatch, raises=True)
    route = _route()
    _apply_jev_complexity_route("…", route)
    assert route["model"] == "session-model"


def test_an_empty_message_is_never_classified(monkeypatch):
    _stub_policy(monkeypatch, tier_model=TierModel(model="big-model"))
    route = _route()
    _apply_jev_complexity_route("   ", route)
    assert route["model"] == "session-model"


def test_a_band_naming_the_current_model_is_a_no_op(monkeypatch):
    _stub_policy(monkeypatch, tier_model=TierModel(model="session-model"))
    route = _route()
    before = route["signature"]
    _apply_jev_complexity_route("…", route)
    assert route["signature"] == before


# --- provider-pinned bands -------------------------------------------------


def test_a_band_pinned_to_another_provider_swaps_the_credentials_too(monkeypatch):
    import gateway.run as gateway_run

    _stub_policy(monkeypatch, tier_model=TierModel(model="big-model", provider="anthropic"))
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs_for_provider",
        lambda provider: {
            "api_key": "sk-anthropic",
            "base_url": "https://anthropic.test",
            "provider": "anthropic",
            "requested_provider": "anthropic",
            "api_mode": "messages",
            "command": None,
            "args": [],
            "credential_pool": None,
        },
    )
    route = _route()
    _apply_jev_complexity_route("…", route)

    assert route["model"] == "big-model"
    assert route["runtime"]["api_key"] == "sk-anthropic"
    assert route["runtime"]["base_url"] == "https://anthropic.test"
    assert route["runtime"]["provider"] == "anthropic"
    assert route["signature"][1] == "anthropic"


def test_a_pinned_provider_whose_credentials_do_not_resolve_keeps_the_session_model(
    monkeypatch,
):
    import gateway.run as gateway_run

    _stub_policy(monkeypatch, tier_model=TierModel(model="big-model", provider="anthropic"))

    def _boom(provider):
        raise RuntimeError("no credentials for anthropic")

    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs_for_provider", _boom)
    route = _route()
    _apply_jev_complexity_route("…", route)

    assert route["model"] == "session-model"
    assert route["runtime"]["api_key"] == "sk-session"


def test_api_mode_is_re_derived_for_the_band_model_on_the_same_provider(monkeypatch):
    import hermes_cli.runtime_provider as runtime_provider

    _stub_policy(monkeypatch, tier_model=TierModel(model="big-model"))
    seen = {}

    def _resolve(*, requested=None, target_model=None, **_kwargs):
        seen["requested"] = requested
        seen["target_model"] = target_model
        return {"api_mode": "responses"}

    monkeypatch.setattr(runtime_provider, "resolve_runtime_provider", _resolve)
    route = _route()
    _apply_jev_complexity_route("…", route)

    assert seen == {"requested": "openai", "target_model": "big-model"}
    assert route["runtime"]["api_mode"] == "responses"
    assert route["signature"][4] == "responses"


def test_an_api_mode_lookup_failure_keeps_the_existing_mode(monkeypatch):
    import hermes_cli.runtime_provider as runtime_provider

    _stub_policy(monkeypatch, tier_model=TierModel(model="big-model"))

    def _boom(**_kwargs):
        raise RuntimeError("provider registry unavailable")

    monkeypatch.setattr(runtime_provider, "resolve_runtime_provider", _boom)
    route = _route()
    _apply_jev_complexity_route("…", route)

    assert route["model"] == "big-model"
    assert route["runtime"]["api_mode"] == "chat"
