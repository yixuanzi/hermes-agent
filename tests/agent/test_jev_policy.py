"""Settings resolution, feature gating and decisions for the Jev policy layer."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent import jev_policy
from agent.jev_client import _parse_answer
from agent.jev_policy import TierModel


_JEV_ENV = [
    "TYPESAFE_API_KEY", "TYPESAFE_BASE_URL", "TYPESAFE_MODEL",
    "HERMES_JEV_TIMEOUT", "HERMES_JEV_CHANNEL_AUTOREPLY",
    "HERMES_JEV_COMPLEXITY_ROUTING", "HERMES_JEV_BUSINESS_SCOPE",
    "HERMES_JEV_RELEVANCE_THRESHOLD", "HERMES_JEV_MIN_CONFIDENCE",
    "HERMES_JEV_MODEL_LOW", "HERMES_JEV_MODEL_MEDIUM", "HERMES_JEV_MODEL_HIGH",
    "HERMES_JEV_PROVIDER_LOW", "HERMES_JEV_PROVIDER_MEDIUM", "HERMES_JEV_PROVIDER_HIGH",
]


@pytest.fixture
def jev_env(monkeypatch):
    """No Jev env and no Jev config.yaml section unless a test supplies one."""
    for name in _JEV_ENV:
        monkeypatch.delenv(name, raising=False)
    config: dict = {}
    monkeypatch.setattr(jev_policy, "_jev_config", lambda: config)
    return config


def _noul(probability: float):
    return _parse_answer("noul", {"type": "noul", "noul": probability})


def _score(label: str, confidence: float):
    return _parse_answer(
        "score",
        {
            "type": "score",
            "legend": {"0": "low", "1": "medium", "2": "high"},
            "probabilities": {
                str(i): (1.0 if name == label else 0.0)
                for i, name in enumerate(("low", "medium", "high"))
            },
            "confidence": confidence,
        },
    )


class _StubClient:
    """Stands in for JevClient; records the questions it was asked."""

    def __init__(self, answers):
        self._answers = answers
        self.asked = []

    def ask(self, state, questions, **_kwargs):
        self.asked.append((state, dict(questions)))
        if self._answers is None:
            return None
        return {name: self._answers[name] for name in questions if name in self._answers}


# --- settings resolution ---------------------------------------------------


def test_defaults_apply_with_no_env_and_no_config(jev_env):
    settings = jev_policy.load_jev_settings()
    assert settings.api_key == ""
    assert settings.configured is False
    assert settings.base_url == jev_policy.DEFAULT_JEV_BASE_URL
    assert settings.model == jev_policy.DEFAULT_JEV_MODEL
    assert settings.channel_autoreply is False
    assert settings.complexity_routing is False
    assert settings.relevance_threshold == pytest.approx(0.7)
    assert settings.min_confidence == pytest.approx(0.5)
    assert settings.tier_models == {}


def test_config_yaml_supplies_settings_when_no_env_is_set(jev_env):
    jev_env.update(
        {
            "base_url": "https://cfg.test",
            "model": "jev-cfg",
            "timeout": 3,
            "channel_autoreply": True,
            "complexity_routing": True,
            "business_scope": "from config",
            "relevance_threshold": 0.9,
            "min_confidence": 0.25,
            "models": {"low": "cfg-low", "high": {"model": "cfg-high", "provider": "openai"}},
        }
    )
    settings = jev_policy.load_jev_settings()
    assert settings.base_url == "https://cfg.test"
    assert settings.model == "jev-cfg"
    assert settings.timeout == pytest.approx(3.0)
    assert settings.channel_autoreply is True
    assert settings.complexity_routing is True
    assert settings.business_scope == "from config"
    assert settings.relevance_threshold == pytest.approx(0.9)
    assert settings.min_confidence == pytest.approx(0.25)
    assert settings.tier_models["low"] == TierModel(model="cfg-low")
    assert settings.tier_models["high"] == TierModel(model="cfg-high", provider="openai")
    assert "medium" not in settings.tier_models


def test_env_wins_over_config_yaml(jev_env, monkeypatch):
    jev_env.update(
        {
            "base_url": "https://cfg.test",
            "channel_autoreply": False,
            "business_scope": "from config",
            "models": {"high": "cfg-high"},
        }
    )
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://env.test")
    monkeypatch.setenv("HERMES_JEV_CHANNEL_AUTOREPLY", "true")
    monkeypatch.setenv("HERMES_JEV_BUSINESS_SCOPE", "from env")
    monkeypatch.setenv("HERMES_JEV_MODEL_HIGH", "env-high")

    settings = jev_policy.load_jev_settings()
    assert settings.base_url == "https://env.test"
    assert settings.channel_autoreply is True
    assert settings.business_scope == "from env"
    assert settings.tier_models["high"] == TierModel(model="env-high")


@pytest.mark.parametrize(
    "raw, expected",
    [("true", True), ("1", True), ("yes", True), ("on", True),
     ("false", False), ("0", False), ("off", False), ("", False), ("garbage", False)],
)
def test_flag_parsing(jev_env, monkeypatch, raw, expected):
    monkeypatch.setenv("HERMES_JEV_COMPLEXITY_ROUTING", raw)
    assert jev_policy.load_jev_settings().complexity_routing is expected


def test_a_malformed_threshold_falls_back_to_the_default(jev_env, monkeypatch):
    monkeypatch.setenv("HERMES_JEV_RELEVANCE_THRESHOLD", "not-a-number")
    assert jev_policy.load_jev_settings().relevance_threshold == pytest.approx(0.7)


def test_an_unreadable_config_yields_an_empty_section_rather_than_raising(monkeypatch):
    import hermes_cli.config as hermes_config

    def _boom():
        raise RuntimeError("config.yaml is unreadable")

    monkeypatch.setattr(hermes_config, "load_config_readonly", _boom)
    assert jev_policy._jev_config() == {}


def test_a_non_dict_jev_section_is_ignored(monkeypatch):
    import hermes_cli.config as hermes_config

    monkeypatch.setattr(hermes_config, "load_config_readonly", lambda: {"jev": "nope"})
    assert jev_policy._jev_config() == {}


def test_settings_still_resolve_when_the_config_file_is_unreadable(monkeypatch):
    import hermes_cli.config as hermes_config

    for name in _JEV_ENV:
        monkeypatch.delenv(name, raising=False)

    def _boom():
        raise RuntimeError("config.yaml is unreadable")

    monkeypatch.setattr(hermes_config, "load_config_readonly", _boom)
    settings = jev_policy.load_jev_settings()
    assert settings.configured is False
    assert settings.channel_autoreply is False
    assert settings.complexity_routing is False


# --- feature gates ---------------------------------------------------------


def test_channel_autoreply_needs_flag_key_and_scope(jev_env, monkeypatch):
    monkeypatch.setenv("HERMES_JEV_CHANNEL_AUTOREPLY", "true")
    assert jev_policy.channel_autoreply_active() is False, "no API key"

    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    assert jev_policy.channel_autoreply_active() is False, "no business scope"

    monkeypatch.setenv("HERMES_JEV_BUSINESS_SCOPE", "security operations")
    assert jev_policy.channel_autoreply_active() is True


def test_channel_autoreply_stays_off_while_the_flag_is_off(jev_env, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    monkeypatch.setenv("HERMES_JEV_BUSINESS_SCOPE", "security operations")
    assert jev_policy.channel_autoreply_active() is False


def test_complexity_routing_needs_flag_key_and_at_least_one_band(jev_env, monkeypatch):
    monkeypatch.setenv("HERMES_JEV_COMPLEXITY_ROUTING", "true")
    assert jev_policy.complexity_routing_active() is False, "no API key"

    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    assert jev_policy.complexity_routing_active() is False, "no band model"

    monkeypatch.setenv("HERMES_JEV_MODEL_HIGH", "big-model")
    assert jev_policy.complexity_routing_active() is True


def test_build_client_returns_none_without_a_key(jev_env):
    assert jev_policy.build_client() is None


def test_build_client_carries_the_resolved_settings(jev_env, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://env.test")
    monkeypatch.setenv("TYPESAFE_MODEL", "jev-x")
    client = jev_policy.build_client()
    assert (client.api_key, client.base_url, client.model) == ("k", "https://env.test", "jev-x")


# --- channel relevance -----------------------------------------------------


def _settings(**overrides):
    base = dict(api_key="k", business_scope="security operations", channel_autoreply=True)
    base.update(overrides)
    return jev_policy.JevSettings(**base)


def test_a_message_is_admitted_only_when_both_propositions_clear_the_threshold():
    client = _StubClient({"in_scope": _noul(0.98), "wants_answer": _noul(0.97)})
    assert jev_policy.judge_channel_relevance("…", settings=_settings(), client=client) is True


def test_an_in_scope_statement_that_asks_nothing_is_not_answered():
    client = _StubClient({"in_scope": _noul(0.97), "wants_answer": _noul(0.04)})
    assert jev_policy.judge_channel_relevance("…", settings=_settings(), client=client) is False


def test_an_off_topic_question_is_not_answered():
    client = _StubClient({"in_scope": _noul(0.17), "wants_answer": _noul(0.96)})
    assert jev_policy.judge_channel_relevance("…", settings=_settings(), client=client) is False


def test_the_threshold_moves_the_admission_bar():
    client = _StubClient({"in_scope": _noul(0.8), "wants_answer": _noul(0.8)})
    assert jev_policy.judge_channel_relevance("…", settings=_settings(), client=client) is True
    client = _StubClient({"in_scope": _noul(0.8), "wants_answer": _noul(0.8)})
    strict = _settings(relevance_threshold=0.95)
    assert jev_policy.judge_channel_relevance("…", settings=strict, client=client) is False


def test_both_propositions_ride_in_one_request():
    client = _StubClient({"in_scope": _noul(0.9), "wants_answer": _noul(0.9)})
    jev_policy.judge_channel_relevance(
        "hello", settings=_settings(), client=client, channel_name="ops", sender_name="zhang",
    )
    assert len(client.asked) == 1
    state, questions = client.asked[0]
    assert set(questions) == {"in_scope", "wants_answer"}
    assert state["business_scope"] == "security operations"
    assert state["message"] == "hello"
    assert state["channel"] == "ops"
    assert state["sender"] == "zhang"


def test_a_partial_answer_is_undecided_not_a_yes():
    client = _StubClient({"in_scope": _noul(0.99)})
    assert jev_policy.judge_channel_relevance("…", settings=_settings(), client=client) is None


def test_an_unreachable_service_is_undecided():
    assert (
        jev_policy.judge_channel_relevance("…", settings=_settings(), client=_StubClient(None))
        is None
    )


def test_empty_text_is_never_sent_for_judgement():
    client = _StubClient({"in_scope": _noul(0.9), "wants_answer": _noul(0.9)})
    assert jev_policy.judge_channel_relevance("   ", settings=_settings(), client=client) is None
    assert client.asked == []


@pytest.mark.asyncio
async def test_judge_channel_relevance_async_mirrors_the_sync_call():
    client = _StubClient({"in_scope": _noul(0.98), "wants_answer": _noul(0.97)})
    verdict = await jev_policy.judge_channel_relevance_async(
        "…", settings=_settings(), client=client
    )
    assert verdict is True


# --- complexity ------------------------------------------------------------


@pytest.mark.parametrize("band", ["low", "medium", "high"])
def test_a_confident_band_is_returned(band):
    client = _StubClient({"complexity": _score(band, 0.9)})
    settings = jev_policy.JevSettings(api_key="k")
    assert jev_policy.judge_complexity("…", settings=settings, client=client) == band


def test_a_band_below_min_confidence_is_discarded():
    client = _StubClient({"complexity": _score("high", 0.26)})
    settings = jev_policy.JevSettings(api_key="k", min_confidence=0.5)
    assert jev_policy.judge_complexity("…", settings=settings, client=client) is None


def test_lowering_min_confidence_accepts_the_same_answer():
    client = _StubClient({"complexity": _score("high", 0.26)})
    settings = jev_policy.JevSettings(api_key="k", min_confidence=0.2)
    assert jev_policy.judge_complexity("…", settings=settings, client=client) == "high"


def test_an_unreachable_service_yields_no_band():
    settings = jev_policy.JevSettings(api_key="k")
    assert jev_policy.judge_complexity("…", settings=settings, client=_StubClient(None)) is None


def test_empty_text_is_not_classified():
    client = _StubClient({"complexity": _score("high", 0.9)})
    settings = jev_policy.JevSettings(api_key="k")
    assert jev_policy.judge_complexity("  ", settings=settings, client=client) is None
    assert client.asked == []


@pytest.mark.asyncio
async def test_judge_complexity_async_mirrors_the_sync_call():
    client = _StubClient({"complexity": _score("medium", 0.9)})
    settings = jev_policy.JevSettings(api_key="k")
    assert await jev_policy.judge_complexity_async("…", settings=settings, client=client) == "medium"


# --- band → model ----------------------------------------------------------


def test_resolve_tier_model_returns_none_for_an_unconfigured_band():
    settings = jev_policy.JevSettings(tier_models={"high": TierModel(model="big")})
    assert jev_policy.resolve_tier_model("high", settings).model == "big"
    assert jev_policy.resolve_tier_model("low", settings) is None
    assert jev_policy.resolve_tier_model(None, settings) is None


def test_route_model_for_request_is_a_no_op_while_routing_is_off(jev_env):
    client = _StubClient({"complexity": _score("high", 0.9)})
    assert jev_policy.route_model_for_request("…", client=client) is None
    assert client.asked == []


def test_route_model_for_request_returns_the_band_model():
    settings = jev_policy.JevSettings(
        api_key="k",
        complexity_routing=True,
        tier_models={"high": TierModel(model="big-model")},
    )
    client = _StubClient({"complexity": _score("high", 0.9)})
    assert jev_policy.route_model_for_request("…", settings=settings, client=client).model == "big-model"


def test_a_band_with_no_configured_model_keeps_the_default():
    settings = jev_policy.JevSettings(
        api_key="k",
        complexity_routing=True,
        tier_models={"high": TierModel(model="big-model")},
    )
    client = _StubClient({"complexity": _score("low", 0.9)})
    assert jev_policy.route_model_for_request("…", settings=settings, client=client) is None


# --- applying a band to a long-lived agent ---------------------------------


def _switchable(*, model, requested_provider, base_url="https://base.test"):
    """A stand-in agent implementing the AIAgent.switch_model contract."""

    class _Agent(SimpleNamespace):
        def switch_model(self, *, new_model, new_provider, api_key="", base_url="",
                         api_mode=""):
            self.model = new_model
            self.provider = new_provider
            self.requested_provider = new_provider
            self.api_key = api_key
            self.base_url = base_url
            self.api_mode = api_mode

    return _Agent(model=model, provider=requested_provider.split(":")[0],
                  requested_provider=requested_provider, api_key="k",
                  base_url=base_url, api_mode="openai_chat")


def _stub_credentials(monkeypatch, by_provider):
    """Make resolve_runtime_provider return a base_url per provider id."""
    import hermes_cli.runtime_provider as rp

    def _resolve(*, requested=None, target_model=None, **_kwargs):
        if requested not in by_provider:
            raise RuntimeError(f"no credentials for {requested}")
        return {
            "provider": requested.split(":")[0],
            "api_key": f"k-{requested}",
            "base_url": by_provider[requested],
            "api_mode": "openai_chat",
        }

    monkeypatch.setattr(rp, "resolve_runtime_provider", _resolve)



def test_the_agent_model_is_swapped_for_the_band():
    agent = SimpleNamespace(model="default-model", provider="openai")
    assert jev_policy.apply_tier_to_agent(agent, TierModel(model="big")) == "big"
    assert agent.model == "big"


def test_no_band_restores_the_agents_own_model():
    agent = SimpleNamespace(model="default-model", provider="openai")
    jev_policy.apply_tier_to_agent(agent, TierModel(model="big"))
    jev_policy.apply_tier_to_agent(agent, None)
    assert agent.model == "default-model"


def test_the_baseline_is_captured_once_and_survives_repeated_banding():
    agent = SimpleNamespace(model="default-model", provider="openai")
    jev_policy.apply_tier_to_agent(agent, TierModel(model="big"))
    jev_policy.apply_tier_to_agent(agent, TierModel(model="small"))
    assert agent.model == "small"
    jev_policy.apply_tier_to_agent(agent, None)
    assert agent.model == "default-model"


def test_a_band_pinned_to_another_provider_switches_the_agent(monkeypatch):
    agent = _switchable(model="default-model", requested_provider="openai")
    _stub_credentials(monkeypatch, {"anthropic": "https://anthropic.test"})
    jev_policy.apply_tier_to_agent(agent, TierModel(model="big", provider="anthropic"))
    assert agent.model == "big"
    assert agent.requested_provider == "anthropic"
    assert agent.base_url == "https://anthropic.test"


def test_a_cross_provider_band_whose_credentials_fail_leaves_the_agent_alone(monkeypatch):
    import hermes_cli.runtime_provider as rp

    def _boom(**_kwargs):
        raise RuntimeError("no credentials for anthropic")

    monkeypatch.setattr(rp, "resolve_runtime_provider", _boom)
    agent = _switchable(model="default-model", requested_provider="openai")
    jev_policy.apply_tier_to_agent(agent, TierModel(model="big", provider="anthropic"))
    assert agent.model == "default-model"
    assert agent.requested_provider == "openai"


def test_a_failing_switch_leaves_the_agent_on_its_current_runtime(monkeypatch):
    agent = _switchable(model="default-model", requested_provider="openai")
    _stub_credentials(monkeypatch, {"anthropic": "https://anthropic.test"})

    def _boom(**_kwargs):
        raise RuntimeError("client rebuild failed")

    agent.switch_model = _boom
    jev_policy.apply_tier_to_agent(agent, TierModel(model="big", provider="anthropic"))
    assert agent.model == "default-model"
    assert agent.requested_provider == "openai"


def test_an_agent_without_switch_model_is_left_alone(monkeypatch):
    _stub_credentials(monkeypatch, {"anthropic": "https://anthropic.test"})
    agent = SimpleNamespace(model="default-model", provider="openai",
                            requested_provider="openai")
    jev_policy.apply_tier_to_agent(agent, TierModel(model="big", provider="anthropic"))
    assert agent.model == "default-model"


def test_no_band_restores_the_profile_provider_too(monkeypatch):
    agent = _switchable(model="default-model", requested_provider="openai",
                        base_url="https://openai.test")
    _stub_credentials(monkeypatch, {"anthropic": "https://anthropic.test"})
    jev_policy.apply_tier_to_agent(agent, TierModel(model="big", provider="anthropic"))
    assert agent.requested_provider == "anthropic"

    jev_policy.apply_tier_to_agent(agent, None)
    assert agent.model == "default-model"
    assert agent.requested_provider == "openai"
    assert agent.base_url == "https://openai.test"


def test_switching_back_and_forth_keeps_the_original_baseline(monkeypatch):
    agent = _switchable(model="default-model", requested_provider="openai",
                        base_url="https://openai.test")
    _stub_credentials(monkeypatch, {"anthropic": "https://anthropic.test",
                                    "xai": "https://xai.test"})
    jev_policy.apply_tier_to_agent(agent, TierModel(model="a", provider="anthropic"))
    jev_policy.apply_tier_to_agent(agent, TierModel(model="b", provider="xai"))
    jev_policy.apply_tier_to_agent(agent, None)
    assert (agent.model, agent.requested_provider, agent.base_url) == (
        "default-model", "openai", "https://openai.test",
    )


def test_a_band_pinned_to_the_agents_own_provider_still_applies():
    agent = SimpleNamespace(model="default-model", provider="OpenAI")
    jev_policy.apply_tier_to_agent(agent, TierModel(model="big", provider="openai"))
    assert agent.model == "big"


# --- provider identity: full id vs canonicalized namespace ------------------
#
# A profile requests `custom:glm`; the runtime canonicalizes that to the bare
# `custom` namespace on `agent.provider`. Comparing a band's full id against
# the namespace rejects a band pinned to the provider the agent is ALREADY on,
# which silently disabled every custom-provider band on this surface.


@pytest.mark.parametrize(
    "configured, pinned, expected",
    [
        ("custom:glm", "custom:glm", True),
        ("glm", "custom:glm", True),           # bare name is the same provider
        ("custom:glm", "glm", True),
        ("CUSTOM:GLM", "custom:glm", True),    # case-insensitive
        ("custom:glm", "custom:chatai", False),
        ("custom", "custom:glm", False),       # namespace alone names no provider
        ("custom:glm", "custom", False),
        ("openai", "openai", True),
        ("openai", "anthropic", False),
        (None, "custom:glm", False),
        ("custom:glm", None, False),
        ("", "", False),
    ],
)
def test_provider_ids_match(configured, pinned, expected):
    assert jev_policy._provider_ids_match(configured, pinned) is expected


def test_a_custom_provider_band_applies_when_the_agent_is_on_that_provider():
    # The regression: agent.provider is the canonicalized namespace, so the
    # band must be matched against requested_provider instead.
    agent = SimpleNamespace(
        model="glm-5.3-flash", provider="custom", requested_provider="custom:glm",
    )
    jev_policy.apply_tier_to_agent(agent, TierModel(model="glm-5.3", provider="custom:glm"))
    assert agent.model == "glm-5.3"


def test_a_custom_provider_band_for_a_different_custom_entry_is_still_skipped():
    agent = SimpleNamespace(
        model="glm-5.3-flash", provider="custom", requested_provider="custom:glm",
    )
    jev_policy.apply_tier_to_agent(
        agent, TierModel(model="gpt-5.6-sol", provider="custom:chatai")
    )
    assert agent.model == "glm-5.3-flash"


def test_without_requested_provider_a_bare_custom_namespace_cannot_be_verified():
    # Nothing identifies WHICH custom provider the agent uses, so the safe
    # answer is to keep the profile's model rather than guess an endpoint.
    agent = SimpleNamespace(model="glm-5.3-flash", provider="custom")
    jev_policy.apply_tier_to_agent(agent, TierModel(model="glm-5.3", provider="custom:glm"))
    assert agent.model == "glm-5.3-flash"


def test_a_cross_provider_switch_is_logged_with_both_sides(monkeypatch, caplog):
    caplog.set_level("INFO")
    _stub_credentials(monkeypatch, {"custom:chatai": "https://chatai.test"})
    agent = _switchable(model="glm-5.3-flash", requested_provider="custom:glm")
    jev_policy.apply_tier_to_agent(
        agent, TierModel(model="gpt-5.6-sol", provider="custom:chatai")
    )
    line = [r.getMessage() for r in caplog.records if "[Jev]" in r.getMessage()][-1]
    assert "gpt-5.6-sol" in line and "custom:chatai" in line
    assert "glm-5.3-flash" in line and "custom:glm" in line
    assert agent.model == "gpt-5.6-sol"


def test_applying_a_band_to_nothing_is_harmless():
    assert jev_policy.apply_tier_to_agent(None, TierModel(model="big")) is None


# --- decision logging ------------------------------------------------------
#
# Operators diagnose "why didn't the bot reply?" and "why is this turn on the
# expensive model?" from these lines, so each decision has to state its outcome
# and how long it took -- including the paths that return no decision at all.


def _jev_records(caplog):
    return [r for r in caplog.records if "[Jev]" in r.getMessage()]


def test_an_admission_logs_its_verdict_and_latency(caplog):
    caplog.set_level("INFO")
    client = _StubClient({"in_scope": _noul(0.98), "wants_answer": _noul(0.97)})
    jev_policy.judge_channel_relevance(
        "…", settings=_settings(), client=client, channel_name="ops",
    )

    line = _jev_records(caplog)[-1].getMessage()
    assert "ANSWER" in line
    assert "ms" in line
    assert "in_scope=0.98" in line and "wants_answer=0.97" in line
    assert "threshold=0.70" in line


def test_staying_quiet_is_logged_just_as_loudly(caplog):
    caplog.set_level("INFO")
    client = _StubClient({"in_scope": _noul(0.02), "wants_answer": _noul(0.40)})
    jev_policy.judge_channel_relevance("…", settings=_settings(), client=client)

    line = _jev_records(caplog)[-1].getMessage()
    assert "STAY QUIET" in line
    assert "ms" in line


def test_an_undecided_admission_warns_rather_than_passing_silently(caplog):
    caplog.set_level("INFO")
    jev_policy.judge_channel_relevance("…", settings=_settings(), client=_StubClient(None))

    record = _jev_records(caplog)[-1]
    assert record.levelname == "WARNING"
    assert "UNDECIDED" in record.getMessage()
    assert "ms" in record.getMessage()


def test_an_accepted_band_logs_its_label_confidence_and_latency(caplog):
    caplog.set_level("INFO")
    client = _StubClient({"complexity": _score("high", 0.9)})
    settings = jev_policy.JevSettings(api_key="k", min_confidence=0.5)
    jev_policy.judge_complexity("…", settings=settings, client=client)

    line = _jev_records(caplog)[-1].getMessage()
    assert "high" in line
    assert "confidence=0.90" in line
    assert "ms" in line


def test_a_rejected_band_says_why_it_was_rejected(caplog):
    caplog.set_level("INFO")
    client = _StubClient({"complexity": _score("high", 0.22)})
    settings = jev_policy.JevSettings(api_key="k", min_confidence=0.5)
    jev_policy.judge_complexity("…", settings=settings, client=client)

    line = _jev_records(caplog)[-1].getMessage()
    assert "REJECTED" in line
    assert "confidence=0.22" in line and "0.50" in line
    assert "default model" in line


def test_an_undecided_band_warns_rather_than_passing_silently(caplog):
    caplog.set_level("INFO")
    settings = jev_policy.JevSettings(api_key="k")
    jev_policy.judge_complexity("…", settings=settings, client=_StubClient(None))

    record = _jev_records(caplog)[-1]
    assert record.levelname == "WARNING"
    assert "UNDECIDED" in record.getMessage()


def test_skipped_decisions_stay_at_debug(caplog):
    # Empty text and an unconfigured client are ordinary, not noteworthy --
    # they must not fill an operator's log at INFO.
    caplog.set_level("DEBUG")
    settings = jev_policy.JevSettings(api_key="k")
    jev_policy.judge_complexity("   ", settings=settings, client=_StubClient(None))
    jev_policy.judge_channel_relevance("   ", settings=_settings(), client=_StubClient(None))

    records = _jev_records(caplog)
    assert records, "expected a debug trail"
    assert all(r.levelname == "DEBUG" for r in records)


def test_a_client_without_ask_detailed_is_still_timed(caplog):
    # _StubClient implements only the minimal ``ask`` contract; the policy
    # layer times it itself so the log line is never missing its latency.
    caplog.set_level("INFO")
    client = _StubClient({"complexity": _score("low", 0.9)})
    assert not hasattr(client, "ask_detailed")
    settings = jev_policy.JevSettings(api_key="k")
    jev_policy.judge_complexity("…", settings=settings, client=client)

    assert "ms" in _jev_records(caplog)[-1].getMessage()


# --- complexity scope: how often a session is rated -------------------------


@pytest.fixture(autouse=True)
def _clear_session_bands():
    """Each test starts with no session remembered."""
    jev_policy._session_bands.clear()
    yield
    jev_policy._session_bands.clear()


def test_the_scope_defaults_to_session(jev_env):
    assert jev_policy.load_jev_settings().complexity_scope == "session"


@pytest.mark.parametrize("raw, expected", [("session", "session"), ("turn", "turn"),
                                           ("TURN", "turn"), ("  session  ", "session")])
def test_the_scope_is_parsed_case_and_space_insensitively(jev_env, monkeypatch, raw, expected):
    monkeypatch.setenv("HERMES_JEV_COMPLEXITY_SCOPE", raw)
    assert jev_policy.load_jev_settings().complexity_scope == expected


def test_an_unknown_scope_falls_back_to_session_with_a_warning(jev_env, monkeypatch, caplog):
    caplog.set_level("WARNING")
    monkeypatch.setenv("HERMES_JEV_COMPLEXITY_SCOPE", "hourly")
    assert jev_policy.load_jev_settings().complexity_scope == "session"
    assert any("complexity scope" in r.getMessage() for r in caplog.records)


def test_the_scope_comes_from_config_yaml_when_no_env_is_set(jev_env):
    jev_env["complexity_scope"] = "turn"
    assert jev_policy.load_jev_settings().complexity_scope == "turn"


def _routing_settings(scope="session"):
    return jev_policy.JevSettings(
        api_key="k",
        complexity_routing=True,
        complexity_scope=scope,
        tier_models={t: TierModel(model=f"{t}-model") for t in jev_policy.COMPLEXITY_TIERS},
    )


def test_session_scope_rates_once_and_reuses_the_band():
    client = _StubClient({"complexity": _score("high", 0.9)})
    settings = _routing_settings("session")

    first = jev_policy.route_model_for_request("big task", settings=settings,
                                               client=client, session_key="sess-1")
    second = jev_policy.route_model_for_request("thanks", settings=settings,
                                                client=client, session_key="sess-1")
    assert first.model == second.model == "high-model"
    assert len(client.asked) == 1, "the follow-up must not be classified again"


def test_turn_scope_rates_every_turn():
    client = _StubClient({"complexity": _score("high", 0.9)})
    settings = _routing_settings("turn")

    jev_policy.route_model_for_request("big task", settings=settings,
                                       client=client, session_key="sess-1")
    jev_policy.route_model_for_request("thanks", settings=settings,
                                       client=client, session_key="sess-1")
    assert len(client.asked) == 2


def test_turn_scope_can_move_a_conversation_between_bands():
    settings = _routing_settings("turn")
    high = _StubClient({"complexity": _score("high", 0.9)})
    low = _StubClient({"complexity": _score("low", 0.9)})

    assert jev_policy.route_model_for_request("big", settings=settings,
                                              client=high, session_key="s").model == "high-model"
    assert jev_policy.route_model_for_request("hi", settings=settings,
                                              client=low, session_key="s").model == "low-model"


def test_session_scope_keeps_the_first_band_even_when_the_work_changes():
    settings = _routing_settings("session")
    high = _StubClient({"complexity": _score("high", 0.9)})
    low = _StubClient({"complexity": _score("low", 0.9)})

    assert jev_policy.route_model_for_request("big", settings=settings,
                                              client=high, session_key="s").model == "high-model"
    # A second client that would rate `low` is never consulted.
    assert jev_policy.route_model_for_request("hi", settings=settings,
                                              client=low, session_key="s").model == "high-model"
    assert low.asked == []


def test_each_session_is_rated_independently():
    client = _StubClient({"complexity": _score("high", 0.9)})
    settings = _routing_settings("session")

    jev_policy.route_model_for_request("a", settings=settings, client=client, session_key="s1")
    jev_policy.route_model_for_request("b", settings=settings, client=client, session_key="s2")
    assert len(client.asked) == 2


def test_session_scope_without_a_session_key_degrades_to_per_turn():
    client = _StubClient({"complexity": _score("high", 0.9)})
    settings = _routing_settings("session")

    jev_policy.route_model_for_request("a", settings=settings, client=client)
    jev_policy.route_model_for_request("b", settings=settings, client=client)
    assert len(client.asked) == 2


def test_an_undecided_turn_is_not_remembered_so_the_next_turn_retries():
    # One transient failure must not pin a whole session to the default model.
    settings = _routing_settings("session")
    broken = _StubClient(None)
    assert jev_policy.route_model_for_request("a", settings=settings,
                                              client=broken, session_key="s") is None

    working = _StubClient({"complexity": _score("high", 0.9)})
    assert jev_policy.route_model_for_request("b", settings=settings,
                                              client=working, session_key="s").model == "high-model"


def test_a_low_confidence_turn_is_not_remembered_either():
    settings = _routing_settings("session")
    unsure = _StubClient({"complexity": _score("high", 0.1)})
    assert jev_policy.route_model_for_request("a", settings=settings,
                                              client=unsure, session_key="s") is None

    sure = _StubClient({"complexity": _score("low", 0.9)})
    assert jev_policy.route_model_for_request("b", settings=settings,
                                              client=sure, session_key="s").model == "low-model"


def test_forgetting_a_session_makes_the_next_turn_rate_again():
    client = _StubClient({"complexity": _score("high", 0.9)})
    settings = _routing_settings("session")

    jev_policy.route_model_for_request("a", settings=settings, client=client, session_key="s")
    jev_policy.forget_session_band("s")
    jev_policy.route_model_for_request("b", settings=settings, client=client, session_key="s")
    assert len(client.asked) == 2


def test_forgetting_nothing_is_harmless():
    jev_policy.forget_session_band(None)
    jev_policy.forget_session_band("")


def test_a_reused_band_is_logged_with_its_scope(caplog):
    caplog.set_level("INFO")
    client = _StubClient({"complexity": _score("high", 0.9)})
    settings = _routing_settings("session")

    jev_policy.route_model_for_request("a", settings=settings, client=client, session_key="s")
    caplog.clear()
    jev_policy.route_model_for_request("b", settings=settings, client=client, session_key="s")

    line = _jev_records(caplog)[-1].getMessage()
    assert "reused" in line and "scope=session" in line and "high" in line


def test_a_remembered_band_still_honors_the_current_band_model():
    # The band is remembered, not the model: re-pointing a band at another
    # model must take effect on the next turn of an existing session.
    client = _StubClient({"complexity": _score("high", 0.9)})
    settings = _routing_settings("session")
    jev_policy.route_model_for_request("a", settings=settings, client=client, session_key="s")

    repointed = jev_policy.JevSettings(
        api_key="k", complexity_routing=True, complexity_scope="session",
        tier_models={"high": TierModel(model="new-high-model")},
    )
    result = jev_policy.route_model_for_request("b", settings=repointed,
                                                client=client, session_key="s")
    assert result.model == "new-high-model"


def test_the_session_memo_is_bounded():
    settings = _routing_settings("session")
    client = _StubClient({"complexity": _score("low", 0.9)})
    for i in range(jev_policy._SESSION_BAND_MAX_ENTRIES + 10):
        jev_policy.route_model_for_request("x", settings=settings,
                                           client=client, session_key=f"s{i}")
    assert len(jev_policy._session_bands._entries) <= jev_policy._SESSION_BAND_MAX_ENTRIES


def test_a_remembered_band_expires():
    import agent.jev_policy as jp

    settings = _routing_settings("session")
    client = _StubClient({"complexity": _score("high", 0.9)})
    now = [1000.0]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(jp.time, "monotonic", lambda: now[0])
        jev_policy.route_model_for_request("a", settings=settings, client=client, session_key="s")
        now[0] += jp._SESSION_BAND_TTL_SECONDS + 1
        jev_policy.route_model_for_request("b", settings=settings, client=client, session_key="s")
    assert len(client.asked) == 2


# --- band resolution: env and config.yaml express the same thing -----------
#
# HERMES_JEV_MODEL_<BAND> / HERMES_JEV_PROVIDER_<BAND> override
# jev.models.<band> per FIELD, so neither surface silently erases the other.


def test_a_band_provider_can_come_from_the_environment(jev_env, monkeypatch):
    monkeypatch.setenv("HERMES_JEV_MODEL_LOW", "env-low")
    monkeypatch.setenv("HERMES_JEV_PROVIDER_LOW", "openai")
    assert jev_policy.load_jev_settings().tier_models["low"] == TierModel(
        model="env-low", provider="openai"
    )


def test_a_model_env_var_keeps_a_provider_configured_in_yaml(jev_env, monkeypatch):
    jev_env["models"] = {"low": {"model": "cfg-low", "provider": "anthropic"}}
    monkeypatch.setenv("HERMES_JEV_MODEL_LOW", "env-low")
    assert jev_policy.load_jev_settings().tier_models["low"] == TierModel(
        model="env-low", provider="anthropic"
    )


def test_a_provider_env_var_keeps_a_model_configured_in_yaml(jev_env, monkeypatch):
    jev_env["models"] = {"low": {"model": "cfg-low", "provider": "anthropic"}}
    monkeypatch.setenv("HERMES_JEV_PROVIDER_LOW", "openai")
    assert jev_policy.load_jev_settings().tier_models["low"] == TierModel(
        model="cfg-low", provider="openai"
    )


def test_a_provider_env_var_applies_to_the_shorthand_yaml_form(jev_env, monkeypatch):
    jev_env["models"] = {"medium": "cfg-medium"}
    monkeypatch.setenv("HERMES_JEV_PROVIDER_MEDIUM", "xai")
    assert jev_policy.load_jev_settings().tier_models["medium"] == TierModel(
        model="cfg-medium", provider="xai"
    )


def test_each_band_resolves_independently(jev_env, monkeypatch):
    jev_env["models"] = {"low": "cfg-low", "high": {"model": "cfg-high", "provider": "a"}}
    monkeypatch.setenv("HERMES_JEV_PROVIDER_LOW", "openai")
    bands = jev_policy.load_jev_settings().tier_models
    assert bands["low"] == TierModel(model="cfg-low", provider="openai")
    assert bands["high"] == TierModel(model="cfg-high", provider="a")
    assert "medium" not in bands


def test_a_provider_with_no_model_is_ignored_with_a_warning(jev_env, monkeypatch, caplog):
    caplog.set_level("WARNING")
    monkeypatch.setenv("HERMES_JEV_PROVIDER_HIGH", "openai")
    assert "high" not in jev_policy.load_jev_settings().tier_models
    assert any("no model" in r.getMessage() for r in caplog.records)


def test_an_empty_provider_env_var_is_not_a_provider(jev_env, monkeypatch):
    jev_env["models"] = {"low": {"model": "cfg-low", "provider": "anthropic"}}
    monkeypatch.setenv("HERMES_JEV_PROVIDER_LOW", "   ")
    assert jev_policy.load_jev_settings().tier_models["low"].provider == "anthropic"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("plain-model", ("plain-model", None)),
        ({"model": "m", "provider": "p"}, ("m", "p")),
        ({"model": "m"}, ("m", None)),
        ({"provider": "p"}, ("", "p")),
        ({}, ("", None)),
        (None, ("", None)),
        (123, ("", None)),
    ],
)
def test_a_yaml_band_entry_is_read_in_both_forms(raw, expected):
    assert jev_policy._configured_tier(raw) == expected
