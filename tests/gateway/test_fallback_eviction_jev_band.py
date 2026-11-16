"""Only a real fallback evicts the cached agent after a successful turn.

Jev complexity routing runs a turn on a band model by design. Comparing models alone made every
routed turn look like a fallback and rebuilt the agent on every message (restarting any codex
app-server thread it owned), so eviction also requires ``_fallback_activated``.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from gateway.turn_context import TurnContext


@pytest.fixture
def runner_and_ctx():
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig()
    runner._sessions = {}
    runner._evict_cached_agent = MagicMock()
    runner._fallback_baseline_model = lambda *a, **k: "primary-model"
    source = SessionSource(platform=Platform.FEISHU, user_id="ou_1", chat_id="oc_1", chat_type="group")
    ctx = TurnContext(source=source, session_key="agent:main:feishu:group:oc_1")
    ctx.result_holder[0] = {"final_response": "ok"}
    return runner, ctx


def test_a_jev_band_model_is_not_a_fallback(runner_and_ctx):
    runner, ctx = runner_and_ctx
    ctx.agent_holder[0] = SimpleNamespace(model="band-model", provider="custom", _fallback_activated=False)

    runner._run_agent_evict_on_fallback(ctx)

    assert not runner._evict_cached_agent.called


def test_an_activated_fallback_still_evicts(runner_and_ctx):
    runner, ctx = runner_and_ctx
    ctx.agent_holder[0] = SimpleNamespace(model="backup-model", provider="custom", _fallback_activated=True)

    runner._run_agent_evict_on_fallback(ctx)

    assert runner._evict_cached_agent.called
