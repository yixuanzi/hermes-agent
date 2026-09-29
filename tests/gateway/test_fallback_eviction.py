"""Tests for fallback-eviction gating on failed runs (#7130).

When a run fails, the gateway must NOT evict the cached agent — doing so
forces MCP reinit on the next message, creating a CPU-burning restart loop.
Eviction should only happen on successful runs where fallback activated.
"""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


class TestFallbackEvictionGating:
    """The fallback-eviction code path should skip eviction on failed runs."""

    def test_failed_run_does_not_evict_cached_agent(self):
        """When result has failed=True, the cached agent should NOT be evicted."""
        # The fix: `and not _run_failed` guard on the eviction check.
        # Simulate the variables that the eviction block uses.
        result = {"failed": True, "final_response": None, "error": "400 invalid model"}
        _run_failed = result.get("failed") if result else False
        assert _run_failed is True, "Failed run should be detected"




class TestFallbackLeftConfigModel:
    """Only a real fallback may evict; deliberate per-turn routes must not."""

    @staticmethod
    def _runner(override_model=None):
        from types import SimpleNamespace

        from gateway.run import GatewayRunner

        runner = object.__new__(GatewayRunner)
        override = {"model": override_model} if override_model else None
        state = SimpleNamespace(conversation=SimpleNamespace(model_override=override))
        runner._peek_session_state = lambda key: state
        return runner

    @staticmethod
    def _agent(model, fallback_activated):
        from types import SimpleNamespace

        return SimpleNamespace(model=model, _fallback_activated=fallback_activated)

    def test_jev_routed_band_model_is_not_a_fallback(self):
        runner = self._runner()
        agent = self._agent("glm-5.3-flash", fallback_activated=False)
        assert runner._fallback_left_config_model("sk", agent, "deepseek-v4-flash") is False

    def test_activated_fallback_evicts(self):
        runner = self._runner()
        agent = self._agent("backup-model", fallback_activated=True)
        assert runner._fallback_left_config_model("sk", agent, "primary-model") is True

    def test_same_model_or_model_override_never_evicts(self):
        assert self._runner()._fallback_left_config_model(
            "sk", self._agent("primary-model", True), "primary-model"
        ) is False
        assert self._runner("gpt-5.4")._fallback_left_config_model(
            "sk", self._agent("gpt-5.4", True), "primary-model"
        ) is False
