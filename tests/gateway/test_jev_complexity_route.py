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

    def _route_model(_text, settings=None, client=None, session_key=None):
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


# --- session id threading --------------------------------------------------


def test_the_session_id_reaches_the_policy_layer(monkeypatch):
    from agent import jev_policy

    seen = {}
    monkeypatch.setattr(jev_policy, "load_jev_settings", lambda: SimpleNamespace(name="stub"))
    monkeypatch.setattr(jev_policy, "complexity_routing_active", lambda _s: True)

    def _route_model(_text, settings=None, client=None, session_key=None):
        seen["session_key"] = session_key
        return TierModel(model="big-model")

    monkeypatch.setattr(jev_policy, "route_model_for_request", _route_model)
    route = _route()
    _apply_jev_complexity_route("…", route, "sess-abc")
    assert seen["session_key"] == "sess-abc"


def test_a_caller_with_no_session_passes_none(monkeypatch):
    from agent import jev_policy

    seen = {}
    monkeypatch.setattr(jev_policy, "load_jev_settings", lambda: SimpleNamespace(name="stub"))
    monkeypatch.setattr(jev_policy, "complexity_routing_active", lambda _s: True)

    def _route_model(_text, settings=None, client=None, session_key=None):
        seen["session_key"] = session_key
        return None

    monkeypatch.setattr(jev_policy, "route_model_for_request", _route_model)
    _apply_jev_complexity_route("…", _route())
    assert seen["session_key"] is None


def test_the_turn_route_builder_forwards_its_session_id(monkeypatch):
    from agent import jev_policy
    from gateway.run import GatewayRunner

    seen = {}
    monkeypatch.setattr(jev_policy, "load_jev_settings", lambda: SimpleNamespace(name="stub"))
    monkeypatch.setattr(jev_policy, "complexity_routing_active", lambda _s: True)

    def _route_model(_text, settings=None, client=None, session_key=None):
        seen["session_key"] = session_key
        return None

    monkeypatch.setattr(jev_policy, "route_model_for_request", _route_model)

    runner = SimpleNamespace(_service_tier=None)
    bound = GatewayRunner._resolve_turn_agent_config.__get__(runner)
    bound("hello", "gpt-5", _route()["runtime"], session_id="sess-xyz")
    assert seen["session_key"] == "sess-xyz"


# --- /model pins the session's model ---------------------------------------
#
# An explicit `/model` outranks the classifier. The routing call is skipped
# entirely, so a pinned session does not even pay for a decision.


def test_a_pinned_session_keeps_its_model(monkeypatch):
    _stub_policy(monkeypatch, tier_model=TierModel(model="big-model"))
    route = _route()
    _apply_jev_complexity_route("rebuild the auth layer", route, "sess-1", model_pinned=True)
    assert route["model"] == "session-model"


def test_a_pinned_session_is_never_classified(monkeypatch):
    from agent import jev_policy

    asked = []
    monkeypatch.setattr(jev_policy, "load_jev_settings", lambda: SimpleNamespace(name="stub"))
    monkeypatch.setattr(jev_policy, "complexity_routing_active", lambda _s: True)

    def _route_model(_text, settings=None, client=None, session_key=None):
        asked.append(_text)
        return TierModel(model="big-model")

    monkeypatch.setattr(jev_policy, "route_model_for_request", _route_model)
    _apply_jev_complexity_route("…", _route(), "sess-1", model_pinned=True)
    assert asked == [], "a pinned session must not spend a Jev call"


def test_an_unpinned_session_still_routes(monkeypatch):
    _stub_policy(monkeypatch, tier_model=TierModel(model="big-model"))
    route = _route()
    _apply_jev_complexity_route("…", route, "sess-1", model_pinned=False)
    assert route["model"] == "big-model"


def test_the_turn_router_reads_the_pin_from_the_resolved_runtime(monkeypatch):
    from agent import jev_policy
    from gateway.run import GatewayRunner

    _stub_policy(monkeypatch, tier_model=TierModel(model="big-model"))
    runner = SimpleNamespace(_service_tier=None)
    bound = GatewayRunner._resolve_turn_agent_config.__get__(runner)

    pinned = _route()["runtime"] | {"_session_model_pinned": True}
    assert bound("hello", "user-chosen-model", pinned)["model"] == "user-chosen-model"

    unpinned = _route()["runtime"]
    assert bound("hello", "user-chosen-model", unpinned)["model"] == "big-model"


def test_the_pin_marker_never_reaches_the_agent_runtime(monkeypatch):
    # runtime kwargs are splatted into AIAgent(**...), so an internal marker
    # leaking through would be a TypeError at agent construction.
    from gateway.run import GatewayRunner

    _stub_policy(monkeypatch, active=False)
    runner = SimpleNamespace(_service_tier=None)
    bound = GatewayRunner._resolve_turn_agent_config.__get__(runner)

    route = bound("hello", "m", _route()["runtime"] | {"_session_model_pinned": True})
    assert "_session_model_pinned" not in route["runtime"]


# --- the pin is set by the real /model resolution path ---------------------


def _runner_with_override(override: dict):
    """A GatewayRunner whose session 'sess' carries a /model override."""
    from gateway.run import GatewayRunner
    from gateway.session_state import SessionState

    runner = object.__new__(GatewayRunner)
    state = SessionState()
    state.conversation.model_override = override
    runner._sessions = {"sess": state}
    runner._rehydrate_session_model_override = lambda _key: None
    return runner


def test_a_model_override_with_its_own_key_marks_the_runtime_pinned(monkeypatch):
    import gateway.run as gateway_run

    runner = _runner_with_override(
        {"model": "user-chosen", "provider": "openai", "api_key": "sk-user"}
    )
    monkeypatch.setattr(gateway_run, "_resolve_gateway_model", lambda _cfg: "config-model")
    monkeypatch.setattr(gateway_run, "_credential_pool_for_provider", lambda _p: None)

    model, runtime = runner._resolve_session_agent_runtime(session_key="sess")
    assert model == "user-chosen"
    assert runtime.get("_session_model_pinned") is True


def test_a_model_override_without_a_key_still_marks_the_runtime_pinned(monkeypatch):
    import gateway.run as gateway_run

    runner = _runner_with_override({"model": "user-chosen", "provider": "openai"})
    runner.config = None
    runner._apply_session_model_override = lambda _k, m, rk: ("user-chosen", rk)
    monkeypatch.setattr(gateway_run, "_resolve_gateway_model", lambda _cfg: "config-model")
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: {"provider": "openai", "api_key": "sk-env", "base_url": "https://x"},
    )

    model, runtime = runner._resolve_session_agent_runtime(session_key="sess")
    assert model == "user-chosen"
    assert runtime.get("_session_model_pinned") is True


def test_a_session_with_no_override_is_not_marked(monkeypatch):
    import gateway.run as gateway_run

    runner = _runner_with_override(None)
    runner.config = None
    monkeypatch.setattr(gateway_run, "_resolve_gateway_model", lambda _cfg: "config-model")
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: {"provider": "openai", "api_key": "sk-env", "base_url": "https://x"},
    )

    _model, runtime = runner._resolve_session_agent_runtime(session_key="sess")
    assert "_session_model_pinned" not in runtime


# --- per-band reasoning effort ----------------------------------------------
#
# A band's effort rides back on the route as `band_reasoning_config` instead of
# being applied in place: only the caller knows whether the session pinned its
# own effort with /reasoning, which outranks the band.


def test_a_band_effort_rides_back_on_the_route(monkeypatch):
    _stub_policy(
        monkeypatch, tier_model=TierModel(model="big-model", reasoning_effort="xhigh"),
    )
    route = _route()
    _apply_jev_complexity_route("rebuild the auth layer", route)
    assert route["model"] == "big-model"
    assert route["band_reasoning_config"] == {"enabled": True, "effort": "xhigh"}


def test_an_effort_only_band_keeps_the_model_and_the_signature(monkeypatch):
    # Effort is set per turn on the cached agent, so it must not rebuild it.
    _stub_policy(monkeypatch, tier_model=TierModel(reasoning_effort="high"))
    route = _route()
    before = route["signature"]
    _apply_jev_complexity_route("…", route)
    assert route["model"] == "session-model"
    assert route["signature"] == before
    assert route["band_reasoning_config"] == {"enabled": True, "effort": "high"}


def test_a_band_naming_the_current_model_still_applies_its_effort(monkeypatch):
    _stub_policy(
        monkeypatch,
        tier_model=TierModel(model="session-model", reasoning_effort="low"),
    )
    route = _route()
    before = route["signature"]
    _apply_jev_complexity_route("…", route)
    assert route["signature"] == before
    assert route["band_reasoning_config"] == {"enabled": True, "effort": "low"}


def test_a_band_can_turn_thinking_off(monkeypatch):
    _stub_policy(monkeypatch, tier_model=TierModel(reasoning_effort="none"))
    route = _route()
    _apply_jev_complexity_route("谢谢", route)
    assert route["band_reasoning_config"] == {"enabled": False}


def test_a_band_without_an_effort_adds_nothing_to_the_route(monkeypatch):
    _stub_policy(monkeypatch, tier_model=TierModel(model="big-model"))
    route = _route()
    _apply_jev_complexity_route("…", route)
    assert "band_reasoning_config" not in route


def test_a_band_effort_survives_an_unreachable_band_model(monkeypatch):
    # The effort says how hard the request is, which still holds on the model
    # the turn falls back to.
    import gateway.run as gateway_run

    _stub_policy(
        monkeypatch,
        tier_model=TierModel(model="big-model", provider="anthropic", reasoning_effort="high"),
    )

    def _boom(provider):
        raise RuntimeError("no credentials for anthropic")

    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs_for_provider", _boom)
    route = _route()
    _apply_jev_complexity_route("…", route)
    assert route["model"] == "session-model"
    assert route["band_reasoning_config"] == {"enabled": True, "effort": "high"}


def test_a_pinned_session_gets_no_band_effort_either(monkeypatch):
    _stub_policy(
        monkeypatch, tier_model=TierModel(model="big-model", reasoning_effort="xhigh"),
    )
    route = _route()
    _apply_jev_complexity_route("…", route, "sess-1", model_pinned=True)
    assert "band_reasoning_config" not in route


def test_the_turn_route_builder_returns_the_band_effort(monkeypatch):
    from gateway.run import GatewayRunner

    _stub_policy(monkeypatch, tier_model=TierModel(reasoning_effort="max"))
    runner = SimpleNamespace(_service_tier=None)
    bound = GatewayRunner._resolve_turn_agent_config.__get__(runner)
    route = bound("hello", "gpt-5", _route()["runtime"])
    assert route["band_reasoning_config"] == {"enabled": True, "effort": "max"}


def test_the_effort_is_logged_with_the_band(monkeypatch, caplog):
    caplog.set_level("INFO")
    _stub_policy(monkeypatch, tier_model=TierModel(reasoning_effort="high"))
    _apply_jev_complexity_route("…", _route())
    assert any(
        "keeping session-model" in r.getMessage() and "reasoning_effort=high" in r.getMessage()
        for r in caplog.records
    )


# --- where the band effort ranks -------------------------------------------
#
# session /reasoning  >  band effort  >  agent.reasoning_overrides[model]
# >  agent.reasoning_effort


def _reasoning_runner(monkeypatch, *, config, session_override=None):
    import gateway.run as gateway_run
    from gateway.run import GatewayRunner
    from gateway.session_state import SessionState

    monkeypatch.setattr(gateway_run, "_load_gateway_runtime_config", lambda: config)
    runner = object.__new__(GatewayRunner)
    state = SessionState()
    state.conversation.reasoning_override = session_override
    runner._sessions = {"sess": state}
    return runner


_REASONING_CFG = {
    "agent": {
        "reasoning_effort": "medium",
        "reasoning_overrides": {"band-model": "low", "session-model": "high"},
    },
}


def test_a_band_effort_outranks_the_per_model_override(monkeypatch):
    runner = _reasoning_runner(monkeypatch, config=_REASONING_CFG)
    resolved = runner._resolve_session_reasoning_config(
        session_key="sess", model="band-model",
        band_reasoning={"enabled": True, "effort": "xhigh"},
    )
    assert resolved == {"enabled": True, "effort": "xhigh"}


def test_a_session_reasoning_override_outranks_the_band(monkeypatch):
    runner = _reasoning_runner(
        monkeypatch, config=_REASONING_CFG,
        session_override={"enabled": True, "effort": "minimal"},
    )
    resolved = runner._resolve_session_reasoning_config(
        session_key="sess", model="band-model",
        band_reasoning={"enabled": True, "effort": "xhigh"},
    )
    assert resolved == {"enabled": True, "effort": "minimal"}


def test_without_a_band_effort_the_band_models_own_override_applies(monkeypatch):
    # The turn's model is the band's, so its per-model override is the one
    # that counts — not the session model's.
    runner = _reasoning_runner(monkeypatch, config=_REASONING_CFG)
    resolved = runner._resolve_session_reasoning_config(
        session_key="sess", model="band-model", band_reasoning=None,
    )
    assert resolved == {"enabled": True, "effort": "low"}


def test_without_any_override_the_global_effort_applies(monkeypatch):
    runner = _reasoning_runner(monkeypatch, config=_REASONING_CFG)
    resolved = runner._resolve_session_reasoning_config(
        session_key="sess", model="other-model", band_reasoning=None,
    )
    assert resolved == {"enabled": True, "effort": "medium"}


def test_the_band_effort_is_handed_out_as_a_copy(monkeypatch):
    runner = _reasoning_runner(monkeypatch, config=_REASONING_CFG)
    band = {"enabled": True, "effort": "high"}
    resolved = runner._resolve_session_reasoning_config(
        session_key="sess", model="band-model", band_reasoning=band,
    )
    resolved["effort"] = "low"
    assert band["effort"] == "high"
