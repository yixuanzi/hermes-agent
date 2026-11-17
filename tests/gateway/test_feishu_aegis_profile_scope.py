"""The fork's Feishu settings come from the owning profile under a multiplexing gateway.

A secondary profile's adapter is constructed inside that profile's secret scope, where
``os.environ`` still holds the launch profile's values: a bare ``os.getenv`` handed it the launch
profile's card, topic and history switches.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent import secret_scope
from tests.gateway.feishu_helpers import make_adapter_skeleton

_LAUNCH_PROFILE_ENV = {
    "FEISHU_REPLY_THREAD": "false",
    "FEISHU_CARD_OUTPUT": "true",
    "FEISHU_GROUP_HISTORY": "true",
    "FEISHU_CARD_TITLE": "Launch profile",
    "HERMES_FEISHU_CARD_FLUSH_INTERVAL": "9",
}


def _constructed_adapter():
    adapter = make_adapter_skeleton()
    adapter.config = SimpleNamespace(extra={})
    adapter._init_aegis_state()
    return adapter


@pytest.fixture
def launch_profile_env(monkeypatch):
    for name, value in _LAUNCH_PROFILE_ENV.items():
        monkeypatch.setenv(name, value)


def test_a_secondary_profile_never_inherits_the_launch_profiles_feishu_settings(launch_profile_env):
    secret_scope.set_multiplex_active(True)
    token = secret_scope.set_secret_scope({"FEISHU_CARD_OUTPUT": "false", "FEISHU_CARD_TITLE": "Ops"})
    try:
        adapter = _constructed_adapter()
    finally:
        secret_scope.reset_secret_scope(token)
        secret_scope.set_multiplex_active(False)

    assert adapter._card_output_enabled is False  # its own value
    assert adapter._card_copy.main_title == "Ops"
    # Unset in its scope: the defaults, never the launch profile's values.
    assert adapter._reply_thread_enabled() is True
    assert adapter._group_history_enabled is False
    assert adapter._card_manager._flush_interval == pytest.approx(0.4)


def test_a_single_profile_gateway_still_reads_its_environment(launch_profile_env):
    adapter = _constructed_adapter()

    assert adapter._card_output_enabled is True
    assert adapter._card_copy.main_title == "Launch profile"
    assert adapter._reply_thread_enabled() is False
    assert adapter._group_history_enabled is True
    assert adapter._card_manager._flush_interval == pytest.approx(9.0)
