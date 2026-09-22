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


def test_a_band_pinned_to_another_provider_is_skipped_here():
    agent = SimpleNamespace(model="default-model", provider="openai")
    jev_policy.apply_tier_to_agent(agent, TierModel(model="big", provider="anthropic"))
    assert agent.model == "default-model"


def test_a_band_pinned_to_the_agents_own_provider_still_applies():
    agent = SimpleNamespace(model="default-model", provider="OpenAI")
    jev_policy.apply_tier_to_agent(agent, TierModel(model="big", provider="openai"))
    assert agent.model == "big"


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
