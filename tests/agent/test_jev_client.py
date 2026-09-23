"""Answer parsing, caching and failure behavior for the Jev System One client."""

from __future__ import annotations

import pytest

from agent import jev_client
from agent.jev_client import JevClient, _parse_answer, choice, noul, score


# --- question primitives ---------------------------------------------------


def test_primitives_build_the_wire_shapes_the_service_expects():
    assert noul("q?") == {"type": "noul", "instructions": "q?"}
    assert choice("q?", ["a", "b"]) == {
        "type": "choice",
        "instructions": "q?",
        "criteria": {"a": None, "b": None},
    }
    assert score("q?", ["low", "high"]) == {
        "type": "score",
        "instructions": "q?",
        "criteria": ["low", "high"],
    }


# --- answer parsing --------------------------------------------------------


def test_noul_probability_doubles_as_its_confidence():
    decision = _parse_answer("noul", {"type": "noul", "noul": 0.98})
    assert decision.probability == pytest.approx(0.98)
    assert decision.confidence == pytest.approx(0.98)
    assert decision.label is None
    assert decision.is_true is True


def test_noul_below_half_is_false():
    assert _parse_answer("noul", {"type": "noul", "noul": 0.2}).is_true is False


def test_choice_carries_the_winning_option_and_its_distribution():
    decision = _parse_answer(
        "choice",
        {
            "type": "choice",
            "choice": "calm",
            "probabilities": {"calm": 0.9, "angry": 0.1},
            "confidence": 0.9,
        },
    )
    assert decision.label == "calm"
    assert decision.confidence == pytest.approx(0.9)
    assert decision.probabilities["angry"] == pytest.approx(0.1)


def test_score_maps_ordinal_keys_back_through_the_legend():
    decision = _parse_answer(
        "score",
        {
            "type": "score",
            "score": 1.89,
            "legend": {"0": "low", "1": "medium", "2": "high"},
            "probabilities": {"0": 0.0, "1": 0.11, "2": 0.89},
            "confidence": 0.83,
        },
    )
    assert decision.label == "high"
    assert decision.confidence == pytest.approx(0.83)
    assert decision.probabilities == pytest.approx({"low": 0.0, "medium": 0.11, "high": 0.89})


def test_score_takes_the_distribution_argmax_not_the_rounded_expected_value():
    # score=1.0 would round to "medium", but the mass is split 50/50 between
    # the outer bands — the calibrated answer is a band that actually has mass.
    decision = _parse_answer(
        "score",
        {
            "type": "score",
            "score": 1.0,
            "legend": {"0": "low", "1": "medium", "2": "high"},
            "probabilities": {"0": 0.5, "1": 0.0, "2": 0.5},
        },
    )
    assert decision.label in {"low", "high"}
    assert decision.label != "medium"


def test_score_falls_back_to_the_expected_value_when_no_distribution_is_returned():
    decision = _parse_answer(
        "score",
        {"type": "score", "score": 2.0, "legend": {"0": "low", "1": "medium", "2": "high"}},
    )
    assert decision.label == "high"


@pytest.mark.parametrize(
    "payload",
    [None, "nope", {}, {"type": "noul"}, {"type": "choice"}, {"type": "unknown", "x": 1}],
)
def test_unparseable_answers_are_dropped_rather_than_guessed(payload):
    assert _parse_answer("noul", payload) is None


# --- endpoint --------------------------------------------------------------


@pytest.mark.parametrize(
    "base_url",
    ["https://example.test", "https://example.test/", "https://example.test/v1"],
)
def test_endpoint_accepts_a_base_url_given_with_or_without_the_v1_suffix(base_url):
    client = JevClient(api_key="k", base_url=base_url)
    assert client.endpoint == "https://example.test/v1/systemone"


# --- request behavior ------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _install_transport(monkeypatch, payload, calls):
    class _FakeHttpx:
        @staticmethod
        def post(url, **kwargs):
            calls.append(kwargs["json"])
            return _FakeResponse(payload)

    monkeypatch.setitem(__import__("sys").modules, "httpx", _FakeHttpx)


def test_ask_returns_the_decisions_keyed_as_the_caller_asked(monkeypatch):
    calls = []
    _install_transport(
        monkeypatch, {"answers": {"a": {"type": "noul", "noul": 0.8}}}, calls
    )
    client = JevClient(api_key="k", base_url="https://example.test")

    answers = client.ask({"message": "hi"}, {"a": noul("q?")})

    assert answers["a"].probability == pytest.approx(0.8)
    assert calls[0]["state"] == {"message": "hi"}
    assert calls[0]["questions"] == {"a": {"type": "noul", "instructions": "q?"}}


def test_a_repeated_question_is_served_from_cache_without_a_second_round_trip(monkeypatch):
    calls = []
    _install_transport(
        monkeypatch, {"answers": {"a": {"type": "noul", "noul": 0.8}}}, calls
    )
    client = JevClient(api_key="k", base_url="https://example.test")

    client.ask({"message": "hi"}, {"a": noul("q?")})
    client.ask({"message": "hi"}, {"a": noul("q?")})
    assert len(calls) == 1

    # Different state is a different decision and must go over the wire.
    client.ask({"message": "bye"}, {"a": noul("q?")})
    assert len(calls) == 2


def test_only_the_uncached_questions_are_sent(monkeypatch):
    calls = []
    _install_transport(
        monkeypatch,
        {"answers": {"a": {"type": "noul", "noul": 0.8}, "b": {"type": "noul", "noul": 0.2}}},
        calls,
    )
    client = JevClient(api_key="k", base_url="https://example.test")

    client.ask({"m": "x"}, {"a": noul("qa")})
    answers = client.ask({"m": "x"}, {"a": noul("qa"), "b": noul("qb")})

    assert set(answers) == {"a", "b"}
    assert set(calls[1]["questions"]) == {"b"}


def test_use_cache_false_always_goes_over_the_wire(monkeypatch):
    calls = []
    _install_transport(
        monkeypatch, {"answers": {"a": {"type": "noul", "noul": 0.8}}}, calls
    )
    client = JevClient(api_key="k", base_url="https://example.test")

    client.ask({"m": "x"}, {"a": noul("q")}, use_cache=False)
    client.ask({"m": "x"}, {"a": noul("q")}, use_cache=False)
    assert len(calls) == 2


def test_no_api_key_means_no_request_at_all(monkeypatch):
    calls = []
    _install_transport(monkeypatch, {"answers": {}}, calls)
    assert JevClient(api_key="").ask({"m": "x"}, {"a": noul("q")}) is None
    assert calls == []


def test_a_transport_failure_returns_none_instead_of_raising(monkeypatch):
    class _Boom:
        @staticmethod
        def post(url, **kwargs):
            raise RuntimeError("connection reset")

    monkeypatch.setitem(__import__("sys").modules, "httpx", _Boom)
    client = JevClient(api_key="k", base_url="https://example.test")
    assert client.ask({"m": "x"}, {"a": noul("q")}) is None


def test_a_response_without_an_answers_object_returns_none(monkeypatch):
    calls = []
    _install_transport(monkeypatch, {"error": "bad request"}, calls)
    client = JevClient(api_key="k", base_url="https://example.test")
    assert client.ask({"m": "x"}, {"a": noul("q")}) is None


def test_an_unanswered_question_is_absent_rather_than_fabricated(monkeypatch):
    calls = []
    _install_transport(
        monkeypatch, {"answers": {"a": {"type": "noul", "noul": 0.8}}}, calls
    )
    client = JevClient(api_key="k", base_url="https://example.test")

    answers = client.ask({"m": "x"}, {"a": noul("qa"), "b": noul("qb")})
    assert set(answers) == {"a"}


@pytest.mark.asyncio
async def test_ask_async_mirrors_ask(monkeypatch):
    calls = []
    _install_transport(
        monkeypatch, {"answers": {"a": {"type": "noul", "noul": 0.8}}}, calls
    )
    client = JevClient(api_key="k", base_url="https://example.test")

    answers = await client.ask_async({"m": "x"}, {"a": noul("q")})
    assert answers["a"].probability == pytest.approx(0.8)


# --- cache mechanics -------------------------------------------------------


def test_cache_entries_expire(monkeypatch):
    cache = jev_client._DecisionCache(ttl=10.0)
    decision = _parse_answer("noul", {"type": "noul", "noul": 0.5})
    now = [1000.0]
    monkeypatch.setattr(jev_client.time, "monotonic", lambda: now[0])

    cache.put("k", decision)
    assert cache.get("k") is decision
    now[0] += 11.0
    assert cache.get("k") is None


def test_cache_evicts_the_least_recently_used_entry():
    cache = jev_client._DecisionCache(max_entries=2)
    decision = _parse_answer("noul", {"type": "noul", "noul": 0.5})
    cache.put("a", decision)
    cache.put("b", decision)
    cache.get("a")           # refresh a, so b is now oldest
    cache.put("c", decision)
    assert cache.get("b") is None
    assert cache.get("a") is decision
    assert cache.get("c") is decision


# --- call stats ------------------------------------------------------------


def test_stats_report_a_live_call(monkeypatch):
    calls = []
    _install_transport(
        monkeypatch, {"answers": {"a": {"type": "noul", "noul": 0.8}}}, calls
    )
    client = JevClient(api_key="k", base_url="https://example.test")

    _answers, stats = client.ask_detailed({"m": "x"}, {"a": noul("q")})
    assert (stats.asked, stats.from_cache, stats.over_wire) == (1, 0, 1)
    assert stats.source == "api"
    assert stats.error is None
    assert stats.elapsed_ms >= 0


def test_stats_report_a_fully_cached_call(monkeypatch):
    calls = []
    _install_transport(
        monkeypatch, {"answers": {"a": {"type": "noul", "noul": 0.8}}}, calls
    )
    client = JevClient(api_key="k", base_url="https://example.test")

    client.ask({"m": "x"}, {"a": noul("q")})
    _answers, stats = client.ask_detailed({"m": "x"}, {"a": noul("q")})
    assert (stats.from_cache, stats.over_wire) == (1, 0)
    assert stats.source == "cache"
    assert len(calls) == 1


def test_stats_report_a_partially_cached_call(monkeypatch):
    calls = []
    _install_transport(
        monkeypatch,
        {"answers": {"a": {"type": "noul", "noul": 0.8}, "b": {"type": "noul", "noul": 0.2}}},
        calls,
    )
    client = JevClient(api_key="k", base_url="https://example.test")

    client.ask({"m": "x"}, {"a": noul("qa")})
    _answers, stats = client.ask_detailed({"m": "x"}, {"a": noul("qa"), "b": noul("qb")})
    assert (stats.asked, stats.from_cache, stats.over_wire) == (2, 1, 1)
    assert stats.source == "api+cache"


def test_stats_name_the_transport_error(monkeypatch):
    class _Boom:
        @staticmethod
        def post(url, **kwargs):
            raise RuntimeError("connection reset")

    monkeypatch.setitem(__import__("sys").modules, "httpx", _Boom)
    client = JevClient(api_key="k", base_url="https://example.test")

    answers, stats = client.ask_detailed({"m": "x"}, {"a": noul("q")})
    assert answers is None
    assert stats.error == "RuntimeError"


def test_stats_name_a_malformed_body(monkeypatch):
    calls = []
    _install_transport(monkeypatch, {"error": "bad request"}, calls)
    client = JevClient(api_key="k", base_url="https://example.test")

    _answers, stats = client.ask_detailed({"m": "x"}, {"a": noul("q")})
    assert stats.error == "no answers object"


def test_stats_report_an_unconfigured_client():
    answers, stats = JevClient(api_key="").ask_detailed({"m": "x"}, {"a": noul("q")})
    assert answers is None
    assert stats.error == "not configured"
    assert stats.over_wire == 0


# --- choice criteria carry descriptions ------------------------------------
#
# Descriptions are not cosmetic. Measured against the live endpoint, the same
# four complexity options asked about "hi" answered `low` at confidence 0.35
# with bare names and 1.00 with one-line descriptions. The confidence is what
# an operator reads to tell a settled answer from a coin-flip, so bare names
# cost the answer its legibility even though nothing gates on the number.


def test_bare_option_names_still_work():
    question = jev_client.choice("pick one", ["low", "high"])
    assert question["type"] == "choice"
    assert question["criteria"] == {"low": None, "high": None}


def test_a_mapping_sends_each_options_criterion():
    question = jev_client.choice(
        "pick one", {"low": "A lookup.", "high": "A campaign."},
    )
    assert question["criteria"] == {"low": "A lookup.", "high": "A campaign."}


def test_option_order_is_preserved():
    # The caller's order is the order the options are presented in.
    question = jev_client.choice("pick one", {"c": "x", "a": "y", "b": "z"})
    assert list(question["criteria"]) == ["c", "a", "b"]


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_a_blank_criterion_becomes_a_bare_name(blank):
    # An empty string on the wire is not "no description" — it is a
    # description that says nothing. Send the option bare instead.
    question = jev_client.choice("pick one", {"low": blank})
    assert question["criteria"] == {"low": None}


def test_criteria_keys_are_stringified():
    question = jev_client.choice("pick one", {1: "first", 2: "second"})
    assert question["criteria"] == {"1": "first", "2": "second"}
