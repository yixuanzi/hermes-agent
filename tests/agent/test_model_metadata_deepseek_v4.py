"""DeepSeek V4 family context windows: every V4 slug resolves to the family window, not the catch-all."""

from unittest.mock import patch

import pytest

from agent.model_metadata import (
    DEFAULT_CONTEXT_LENGTHS,
    _stale_pre_catalog_cache_entry,
    get_model_context_length,
)

V4_WINDOW = DEFAULT_CONTEXT_LENGTHS["deepseek-v4"]
CATCH_ALL = DEFAULT_CONTEXT_LENGTHS["deepseek"]


@pytest.fixture
def no_live_metadata():
    with patch("agent.model_metadata.fetch_model_metadata", return_value={}), \
         patch("agent.model_metadata.fetch_endpoint_model_metadata", return_value={}), \
         patch("agent.model_metadata.get_cached_context_length", return_value=None):
        yield


def test_the_v4_family_window_is_wider_than_the_pre_v4_catch_all() -> None:
    assert V4_WINDOW > CATCH_ALL


@pytest.mark.parametrize(
    "model_id",
    [
        "deepseek-v4-pro",
        "deepseek-v4-flash",
        "deepseek-v4.1-flash",
        "deepseek-v4.1-pro",
        "deepseek-v4.2-flash",
        "deepseek-v4-flash-0731",
        "DeepSeek-V4.1-Pro",
        "deepseek/deepseek-v4.1-pro",
    ],
)
def test_every_v4_slug_resolves_to_the_family_window(no_live_metadata, model_id: str) -> None:
    assert get_model_context_length(model_id) == V4_WINDOW


def test_pre_v4_ids_keep_the_catch_all(no_live_metadata) -> None:
    assert get_model_context_length("deepseek-v3.2") == CATCH_ALL


def test_custom_endpoint_point_release_without_metadata_uses_the_family_window() -> None:
    with patch("agent.model_metadata.fetch_model_metadata", return_value={}), \
         patch("agent.model_metadata.fetch_endpoint_model_metadata", return_value={"deepseek-v4.1-pro": {}}), \
         patch("agent.model_metadata.get_cached_context_length", return_value=None), \
         patch("agent.model_metadata._query_ollama_api_show", return_value=None), \
         patch("agent.model_metadata._query_local_context_length", return_value=None), \
         patch("agent.model_metadata.is_local_endpoint", return_value=False):
        result = get_model_context_length(
            "deepseek-v4.1-pro", base_url="https://llm-gateway.example.com/v1", api_key="k", provider="custom",
        )
    assert result == V4_WINDOW


def test_a_point_release_cached_at_the_catch_all_is_treated_as_stale() -> None:
    assert _stale_pre_catalog_cache_entry("deepseek-v4.1-pro", CATCH_ALL)
    assert not _stale_pre_catalog_cache_entry("deepseek-v4.1-pro", V4_WINDOW)
