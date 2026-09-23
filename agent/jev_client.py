"""TypeSafe Jev (System One) client — typed, calibrated decisions.

Jev answers *typed questions* about unstructured state in a single forward
pass.  Hermes uses it strictly as a decision surface in front of the agent
loop ("is this channel message our business?", "how hard is this task?") —
it never generates prose and never replaces the conversational model.

Wire contract (``POST {base_url}/v1/systemone``)::

    {"model": "jev-latest",
     "state": {"message": "..."},
     "questions": {"billing": {"type": "noul", "instructions": "..."}}}

    {"answers": {"billing": {"type": "noul", "noul": 0.98}}, ...}

Everything here is best-effort.  A missing key, an unreachable endpoint, a
malformed payload or a timeout returns ``None`` so the caller falls back to
its pre-Jev behavior.  A classifier that fails must never drop a user's
message or strand a turn without a model.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Union

logger = logging.getLogger(__name__)

DEFAULT_JEV_BASE_URL = "https://api.typesafe.ai"
DEFAULT_JEV_MODEL = "jev-latest"
DEFAULT_JEV_TIMEOUT = 8.0

#: Decisions are memoised so the same inbound text classified on two paths
#: (an adapter's relevance gate and the turn router's complexity gate) costs
#: one round trip, not two.  Small and short-lived on purpose: this is a
#: request-coalescing window, not a persistent cache.
_CACHE_MAX_ENTRIES = 256
_CACHE_TTL_SECONDS = 120.0


# ---------------------------------------------------------------------------
# Question primitives
# ---------------------------------------------------------------------------


def noul(instructions: str) -> Dict[str, Any]:
    """A true/false proposition. The answer is a calibrated probability."""
    return {"type": "noul", "instructions": instructions}


def choice(
    instructions: str,
    criteria: Union[Sequence[str], Mapping[str, Optional[str]]],
) -> Dict[str, Any]:
    """Pick one of ``criteria``. The answer carries a per-option distribution.

    ``criteria`` is either bare option names or a mapping of option name to a
    one-line description of when that option applies.  Descriptions are worth
    supplying: measured against the live endpoint, the same four options asked
    about "hi" answered ``low`` at confidence **0.35** with bare names and
    **1.00** with descriptions.  Bare names do not just read worse: a
    thin-spread answer is one the caller has less reason to trust, and the
    logged confidence is what an operator reads to decide whether a criterion
    needs rewriting.
    """
    if isinstance(criteria, Mapping):
        rendered: Dict[str, Optional[str]] = {}
        for name, description in criteria.items():
            text = str(description).strip() if description is not None else ""
            rendered[str(name)] = text or None
    else:
        rendered = {str(name): None for name in criteria}
    return {
        "type": "choice",
        "instructions": instructions,
        "criteria": rendered,
    }


def score(instructions: str, criteria: Sequence[str]) -> Dict[str, Any]:
    """Rate on an *ordered* scale. ``criteria`` runs low → high."""
    return {
        "type": "score",
        "instructions": instructions,
        "criteria": [str(name) for name in criteria],
    }


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JevDecision:
    """One typed answer, normalized across the three primitives.

    ``label`` is the winning option (``None`` for a bare ``noul``) and
    ``confidence`` is Jev's calibrated confidence in it.  For ``noul`` the
    probability *is* the confidence, so both fields carry it.
    """

    kind: str
    label: Optional[str]
    confidence: float
    probability: float
    probabilities: Mapping[str, float]

    @property
    def is_true(self) -> bool:
        """``noul`` convenience: did the proposition hold at p > 0.5?"""
        return self.probability > 0.5


@dataclass(frozen=True)
class JevCallStats:
    """What one ``ask`` cost, so callers can log result and latency together.

    ``elapsed_ms`` covers the whole call including cache lookups, so a fully
    memoised answer legitimately reports ~0ms — ``source`` is what tells the
    two apart in a log line.
    """

    elapsed_ms: float
    asked: int
    from_cache: int
    over_wire: int
    error: Optional[str] = None

    @property
    def source(self) -> str:
        if self.over_wire == 0 and self.asked:
            return "cache"
        if self.from_cache:
            return "api+cache"
        return "api"


def _parse_answer(kind_hint: str, payload: Any) -> Optional[JevDecision]:
    if not isinstance(payload, dict):
        return None
    kind = str(payload.get("type") or kind_hint or "").strip().lower()

    if kind == "noul":
        raw = payload.get("noul")
        if not isinstance(raw, (int, float)):
            return None
        probability = float(raw)
        return JevDecision(
            kind="noul",
            label=None,
            confidence=probability,
            probability=probability,
            probabilities={"true": probability, "false": 1.0 - probability},
        )

    raw_probabilities = payload.get("probabilities")
    probabilities: Dict[str, float] = {}
    if isinstance(raw_probabilities, dict):
        for key, value in raw_probabilities.items():
            if isinstance(value, (int, float)):
                probabilities[str(key)] = float(value)

    if kind == "choice":
        label = payload.get("choice")
        if label is None and probabilities:
            label = max(probabilities, key=lambda k: probabilities[k])
        if label is None:
            return None
        label = str(label)

    elif kind == "score":
        # ``legend`` maps the ordinal index back to the caller's label.  Take
        # the argmax of the distribution rather than rounding the expected
        # value: the distribution is what Jev calibrates, and a 0.5/0.5 split
        # across two adjacent bands must not silently land on the midpoint.
        legend = payload.get("legend")
        legend_map = (
            {str(k): str(v) for k, v in legend.items()} if isinstance(legend, dict) else {}
        )
        if probabilities:
            winner = max(probabilities, key=lambda k: probabilities[k])
            label = legend_map.get(winner, winner)
            probabilities = {
                legend_map.get(k, k): v for k, v in probabilities.items()
            }
        elif isinstance(payload.get("score"), (int, float)) and legend_map:
            label = legend_map.get(str(int(round(float(payload["score"])))))
            if label is None:
                return None
        else:
            return None

    else:
        return None

    raw_confidence = payload.get("confidence")
    probability = probabilities.get(label, 0.0)
    confidence = (
        float(raw_confidence) if isinstance(raw_confidence, (int, float)) else probability
    )
    return JevDecision(
        kind=kind,
        label=label,
        confidence=confidence,
        probability=probability,
        probabilities=probabilities,
    )


# ---------------------------------------------------------------------------
# Decision cache
# ---------------------------------------------------------------------------


class _DecisionCache:
    """Per-question TTL + LRU memo, keyed by the exact request that produced it."""

    def __init__(self, max_entries: int = _CACHE_MAX_ENTRIES, ttl: float = _CACHE_TTL_SECONDS):
        self._entries: "OrderedDict[str, tuple[float, JevDecision]]" = OrderedDict()
        self._lock = threading.Lock()
        self._max_entries = max_entries
        self._ttl = ttl

    def get(self, key: str) -> Optional[JevDecision]:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, decision = entry
            if expires_at <= now:
                self._entries.pop(key, None)
                return None
            self._entries.move_to_end(key)
            return decision

    def put(self, key: str, decision: JevDecision) -> None:
        with self._lock:
            self._entries[key] = (time.monotonic() + self._ttl, decision)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class JevClient:
    """Blocking System One client. Returns ``None`` instead of raising."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_JEV_BASE_URL,
        model: str = DEFAULT_JEV_MODEL,
        timeout: float = DEFAULT_JEV_TIMEOUT,
        cache: Optional[_DecisionCache] = None,
    ):
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or DEFAULT_JEV_BASE_URL).strip().rstrip("/")
        self.model = (model or DEFAULT_JEV_MODEL).strip()
        self.timeout = float(timeout or DEFAULT_JEV_TIMEOUT)
        self._cache = cache if cache is not None else _DecisionCache()

    @property
    def endpoint(self) -> str:
        # Accept a base_url given with or without the ``/v1`` suffix: the same
        # host is commonly configured both ways for OpenAI-compatible gateways.
        base = self.base_url
        if base.endswith("/v1"):
            return f"{base}/systemone"
        return f"{base}/v1/systemone"

    def _cache_key(self, state: Mapping[str, Any], name: str, question: Mapping[str, Any]) -> str:
        payload = json.dumps(
            [self.model, self.endpoint, state, name, question],
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def ask(
        self,
        state: Mapping[str, Any],
        questions: Mapping[str, Mapping[str, Any]],
        *,
        use_cache: bool = True,
    ) -> Optional[Dict[str, JevDecision]]:
        """Answer every question in one round trip.

        Returns the decisions keyed exactly as ``questions`` was, or ``None``
        when the call could not be made at all.  Individual questions the
        service did not answer are simply absent from the result.
        """
        return self.ask_detailed(state, questions, use_cache=use_cache)[0]

    def ask_detailed(
        self,
        state: Mapping[str, Any],
        questions: Mapping[str, Mapping[str, Any]],
        *,
        use_cache: bool = True,
    ) -> tuple[Optional[Dict[str, JevDecision]], JevCallStats]:
        """``ask``, plus what the call cost — for callers that log latency."""
        started = time.monotonic()

        def _elapsed() -> float:
            return (time.monotonic() - started) * 1000.0

        if not self.api_key or not questions:
            return None, JevCallStats(
                elapsed_ms=_elapsed(),
                asked=len(questions),
                from_cache=0,
                over_wire=0,
                error="not configured" if not self.api_key else "no questions",
            )

        keys = {
            name: self._cache_key(state, name, question)
            for name, question in questions.items()
        }
        answers: Dict[str, JevDecision] = {}
        pending: Dict[str, Mapping[str, Any]] = {}
        for name, question in questions.items():
            cached = self._cache.get(keys[name]) if use_cache else None
            if cached is not None:
                answers[name] = cached
            else:
                pending[name] = question

        cached_count = len(answers)

        def _stats(over_wire: int, error: Optional[str] = None) -> JevCallStats:
            return JevCallStats(
                elapsed_ms=_elapsed(),
                asked=len(questions),
                from_cache=cached_count,
                over_wire=over_wire,
                error=error,
            )

        if not pending:
            return answers, _stats(0)

        payload = {
            "model": self.model,
            "state": dict(state),
            "questions": {name: dict(q) for name, q in pending.items()},
        }

        try:
            import httpx

            response = httpx.post(
                self.endpoint,
                json=payload,
                timeout=self.timeout,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            error = exc.__class__.__name__
            logger.warning(
                "[Jev] decision request failed (%s) in %.0fms via %s; caller falls back",
                error,
                _elapsed(),
                self.endpoint,
            )
            logger.debug("[Jev] decision request error detail", exc_info=True)
            return (answers or None), _stats(len(pending), error=error)

        raw_answers = body.get("answers") if isinstance(body, dict) else None
        if not isinstance(raw_answers, dict):
            logger.warning(
                "[Jev] response carried no answers object (%.0fms via %s)",
                _elapsed(),
                self.endpoint,
            )
            return (answers or None), _stats(len(pending), error="no answers object")

        for name, question in pending.items():
            decision = _parse_answer(str(question.get("type") or ""), raw_answers.get(name))
            if decision is None:
                continue
            answers[name] = decision
            if use_cache:
                self._cache.put(keys[name], decision)

        stats = _stats(len(pending))
        logger.debug(
            "[Jev] %d/%d answers in %.0fms (%s) via %s",
            len(answers),
            len(questions),
            stats.elapsed_ms,
            stats.source,
            self.endpoint,
        )
        return (answers or None), stats

    async def ask_async(
        self,
        state: Mapping[str, Any],
        questions: Mapping[str, Mapping[str, Any]],
        *,
        use_cache: bool = True,
    ) -> Optional[Dict[str, JevDecision]]:
        """``ask`` off the event loop — adapters call this from async handlers."""
        import asyncio

        return await asyncio.to_thread(self.ask, state, questions, use_cache=use_cache)
