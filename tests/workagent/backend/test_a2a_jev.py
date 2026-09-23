"""Jev gates on the WORKAGENT A2A executor.

Channel admission only applies to a request the caller *labelled* as an
unmentioned channel message — a plain RPC call must never be silently
refused, because the caller is blocking on an answer.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agent.jev_policy import TierModel
from workagent.backend.a2a_service.executor import (
    HermesA2AExecutor,
    _is_unmentioned_channel_request,
)


# --- which requests the gate applies to ------------------------------------


@pytest.mark.parametrize(
    "source_meta, expected",
    [
        ({}, False),                                                  # plain RPC
        ({"platform": "feishu", "uid": "u1"}, False),                 # no chat_type
        ({"chat_type": "dm"}, False),
        ({"chat_type": "p2p"}, False),
        ({"chat_type": "group"}, True),
        ({"chat_type": "channel"}, True),
        ({"chat_type": "supergroup"}, True),
        ({"chat_type": "GROUP"}, True),                               # case-insensitive
        ({"chat_type": "group", "mentioned": True}, False),
        ({"chat_type": "group", "mentioned": False}, True),
        ({"chat_type": "group", "mention": True}, False),
    ],
)
def test_only_an_unmentioned_channel_request_is_gated(source_meta, expected):
    assert _is_unmentioned_channel_request(source_meta) is expected


# --- the gate itself -------------------------------------------------------


def _executor():
    return HermesA2AExecutor.__new__(HermesA2AExecutor)


def _stub_policy(monkeypatch, *, active=True, verdict=True, raises=False):
    from agent import jev_policy

    monkeypatch.setattr(jev_policy, "load_jev_settings", lambda: SimpleNamespace(name="stub"))
    monkeypatch.setattr(
        jev_policy, "channel_autoreply_active", lambda _s, **_kw: active,
    )

    async def _judge(_text, **_kwargs):
        if raises:
            raise RuntimeError("classifier exploded")
        return verdict

    monkeypatch.setattr(jev_policy, "judge_channel_relevance_async", _judge)


def _in_scope(executor, monkeypatch, **kwargs):
    _stub_policy(monkeypatch, **kwargs)
    return asyncio.run(
        executor._jev_channel_request_in_scope("alert triage please", {"channel": "ops"})
    )


def test_an_in_scope_channel_request_is_answered(monkeypatch):
    assert _in_scope(_executor(), monkeypatch, verdict=True) is True


def test_an_out_of_scope_channel_request_is_declined(monkeypatch):
    assert _in_scope(_executor(), monkeypatch, verdict=False) is False


def test_an_undecided_verdict_answers_rather_than_stranding_the_caller(monkeypatch):
    assert _in_scope(_executor(), monkeypatch, verdict=None) is True


def test_the_gate_is_transparent_while_autoreply_is_off(monkeypatch):
    assert _in_scope(_executor(), monkeypatch, active=False, verdict=False) is True


def test_a_raising_classifier_answers_rather_than_refusing(monkeypatch):
    assert _in_scope(_executor(), monkeypatch, raises=True) is True


def test_an_unavailable_policy_module_answers_normally(monkeypatch):
    executor = _executor()
    monkeypatch.setattr(HermesA2AExecutor, "_jev_settings", staticmethod(lambda: None))
    assert (
        asyncio.run(executor._jev_channel_request_in_scope("…", {})) is True
    )


def test_the_declined_reply_is_an_explicit_marker_not_an_empty_string():
    from workagent.backend.a2a_service.executor import JEV_OUT_OF_SCOPE_REPLY

    assert JEV_OUT_OF_SCOPE_REPLY.strip()
    assert "SKIPPED" in JEV_OUT_OF_SCOPE_REPLY


# --- complexity routing on the cached agent --------------------------------


def _stub_routing(monkeypatch, *, active=True, tier_model=None, raises=False):
    from agent import jev_policy

    monkeypatch.setattr(jev_policy, "load_jev_settings", lambda: SimpleNamespace(name="stub"))
    monkeypatch.setattr(jev_policy, "complexity_routing_active", lambda _s: active)

    def _route_model(_text, settings=None, client=None, session_key=None):
        if raises:
            raise RuntimeError("classifier exploded")
        return tier_model

    monkeypatch.setattr(jev_policy, "route_model_for_request", _route_model)


def test_the_turn_is_routed_to_the_band_model(monkeypatch):
    _stub_routing(monkeypatch, tier_model=TierModel(model="big-model"))
    agent = SimpleNamespace(model="profile-model", provider="openai")
    asyncio.run(_executor()._apply_jev_complexity_route(agent, "rewrite the parser"))
    assert agent.model == "big-model"


def test_a_later_unbanded_turn_restores_the_profile_model(monkeypatch):
    executor = _executor()
    agent = SimpleNamespace(model="profile-model", provider="openai")

    _stub_routing(monkeypatch, tier_model=TierModel(model="big-model"))
    asyncio.run(executor._apply_jev_complexity_route(agent, "rewrite the parser"))
    assert agent.model == "big-model"

    _stub_routing(monkeypatch, tier_model=None)
    asyncio.run(executor._apply_jev_complexity_route(agent, "hi"))
    assert agent.model == "profile-model"


def test_routing_off_never_touches_the_agent(monkeypatch):
    _stub_routing(monkeypatch, active=False, tier_model=TierModel(model="big-model"))
    agent = SimpleNamespace(model="profile-model", provider="openai")
    asyncio.run(_executor()._apply_jev_complexity_route(agent, "…"))
    assert agent.model == "profile-model"


def test_a_classifier_failure_never_touches_the_agent(monkeypatch):
    _stub_routing(monkeypatch, raises=True)
    agent = SimpleNamespace(model="profile-model", provider="openai")
    asyncio.run(_executor()._apply_jev_complexity_route(agent, "…"))
    assert agent.model == "profile-model"


def test_an_unavailable_policy_module_never_touches_the_agent(monkeypatch):
    monkeypatch.setattr(HermesA2AExecutor, "_jev_settings", staticmethod(lambda: None))
    agent = SimpleNamespace(model="profile-model", provider="openai")
    asyncio.run(_executor()._apply_jev_complexity_route(agent, "…"))
    assert agent.model == "profile-model"


def test_the_a2a_context_id_is_the_session_identity_for_banding(monkeypatch):
    from agent import jev_policy

    seen = {}
    monkeypatch.setattr(jev_policy, "load_jev_settings", lambda: SimpleNamespace(name="stub"))
    monkeypatch.setattr(jev_policy, "complexity_routing_active", lambda _s: True)

    def _route_model(_text, settings=None, client=None, session_key=None):
        seen["session_key"] = session_key
        return TierModel(model="big-model")

    monkeypatch.setattr(jev_policy, "route_model_for_request", _route_model)
    agent = SimpleNamespace(model="profile-model", provider="openai")
    asyncio.run(_executor()._apply_jev_complexity_route(agent, "task", "ctx-42"))
    assert seen["session_key"] == "ctx-42"


def test_the_profile_provider_id_reaches_the_agent(monkeypatch):
    # apply_tier_to_agent compares a band's provider against the agent's
    # requested_provider; without it, every custom-provider band is skipped.
    from hermes_cli import config as hermes_config
    from workagent.backend.agent_runtime import build_profile_agent_kwargs

    monkeypatch.setattr(
        hermes_config, "load_config_readonly",
        lambda: {"model": {"provider": "custom:glm", "default": "glm-5.3-flash"}},
    )

    class _RuntimeProvider:
        @staticmethod
        def resolve_runtime_provider(*, requested=None, target_model=None):
            return {"provider": "custom", "base_url": "https://glm.test", "api_key": "k"}

    kwargs = build_profile_agent_kwargs(
        "sess", platform="workagent-a2a",
        config_module=hermes_config, runtime_provider_module=_RuntimeProvider,
    )
    assert kwargs["provider"] == "custom"
    assert kwargs["requested_provider"] == "custom:glm"


def test_the_autoreply_master_switch_does_not_reach_this_surface(monkeypatch):
    """HERMES_JEV_AUTOREPLY governs unaddressed GROUP CHAT messages.

    A2A is request/response: the caller blocks on a reply, and nothing in the
    delegate envelope even carries the chat_type/mentioned fields this surface's
    gate keys on. Letting a chat-scoped flag flip an RPC service from "answer"
    to "refuse" would change a contract it was never described as touching, so
    the master switch is deliberately not read here.
    """
    from agent import jev_policy

    executor = _executor()
    monkeypatch.setattr(
        jev_policy, "load_jev_settings",
        lambda: SimpleNamespace(name="stub", autoreply_gate=True),
    )
    monkeypatch.setattr(jev_policy, "channel_autoreply_active", lambda _s, **_kw: False)

    assert asyncio.run(executor._jev_channel_request_in_scope("anything", {})) is True
