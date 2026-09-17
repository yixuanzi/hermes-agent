from __future__ import annotations

from types import SimpleNamespace

from aisoc.backend.agent_runtime import build_profile_agent_kwargs, default_agent_factory


def _fake_cfg_get(cfg, *keys, default=None):
    current = cfg
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def test_build_profile_agent_kwargs_reads_platform_toolsets_from_loaded_config() -> None:
    cfg = {
        "model": {
            "default": "deepseek-v4-flash",
            "provider": "custom:chatai",
        },
        "platform_toolsets": {
            "aisoc_web": ["web", "a2a"],
        },
    }
    config_module = SimpleNamespace(
        load_config_readonly=lambda: cfg,
        cfg_get=_fake_cfg_get,
    )
    runtime_provider_module = SimpleNamespace(
        resolve_runtime_provider=lambda **_: {
            "provider": "custom",
            "model": "gpt-5.4",
            "source": "custom_provider:chatai",
        }
    )

    agent_kwargs = build_profile_agent_kwargs(
        "context-123",
        platform="aisoc_web",
        config_module=config_module,
        runtime_provider_module=runtime_provider_module,
    )

    assert agent_kwargs["enabled_toolsets"] == ["web", "a2a"]


def test_build_profile_agent_kwargs_does_not_enable_a2a_when_platform_toolsets_missing() -> None:
    cfg = {
        "model": {
            "default": "deepseek-v4-flash",
            "provider": "custom:chatai",
        },
    }
    config_module = SimpleNamespace(
        load_config_readonly=lambda: cfg,
        cfg_get=_fake_cfg_get,
    )
    runtime_provider_module = SimpleNamespace(
        resolve_runtime_provider=lambda **_: {
            "provider": "custom",
            "model": "gpt-5.4",
            "source": "custom_provider:chatai",
        }
    )

    agent_kwargs = build_profile_agent_kwargs(
        "context-123",
        platform="aisoc_web",
        config_module=config_module,
        runtime_provider_module=runtime_provider_module,
    )

    assert agent_kwargs["enabled_toolsets"] == ["hermes-cli"]


def test_build_profile_agent_kwargs_respects_explicit_empty_platform_toolsets() -> None:
    cfg = {
        "model": {
            "default": "deepseek-v4-flash",
            "provider": "custom:chatai",
        },
        "platform_toolsets": {
            "aisoc_web": [],
        },
    }
    config_module = SimpleNamespace(
        load_config_readonly=lambda: cfg,
        cfg_get=_fake_cfg_get,
    )
    runtime_provider_module = SimpleNamespace(
        resolve_runtime_provider=lambda **_: {
            "provider": "custom",
            "model": "gpt-5.4",
            "source": "custom_provider:chatai",
        }
    )

    agent_kwargs = build_profile_agent_kwargs(
        "context-123",
        platform="aisoc_web",
        config_module=config_module,
        runtime_provider_module=runtime_provider_module,
    )

    assert agent_kwargs["enabled_toolsets"] == []


def test_build_profile_agent_kwargs_auto_appends_enabled_mcp_servers() -> None:
    cfg = {
        "model": {
            "default": "deepseek-v4-flash",
            "provider": "custom:chatai",
        },
        "mcp_servers": {
            "filesystem": {"enabled": True},
            "github": {"enabled": "yes"},
            "disabled-one": {"enabled": False},
        },
    }
    config_module = SimpleNamespace(
        load_config_readonly=lambda: cfg,
        cfg_get=_fake_cfg_get,
    )
    runtime_provider_module = SimpleNamespace(
        resolve_runtime_provider=lambda **_: {
            "provider": "custom",
            "model": "gpt-5.4",
            "source": "custom_provider:chatai",
        }
    )

    agent_kwargs = build_profile_agent_kwargs(
        "context-123",
        platform="aisoc_web",
        config_module=config_module,
        runtime_provider_module=runtime_provider_module,
    )

    assert agent_kwargs["enabled_toolsets"] == ["hermes-cli", "filesystem", "github"]


def test_build_profile_agent_kwargs_disables_mcp_via_env(
    monkeypatch,
) -> None:
    cfg = {
        "model": {
            "default": "deepseek-v4-flash",
            "provider": "custom:chatai",
        },
        "mcp_servers": {
            "filesystem": {"enabled": True},
        },
    }
    config_module = SimpleNamespace(
        load_config_readonly=lambda: cfg,
        cfg_get=_fake_cfg_get,
    )
    runtime_provider_module = SimpleNamespace(
        resolve_runtime_provider=lambda **_: {
            "provider": "custom",
            "model": "gpt-5.4",
            "source": "custom_provider:chatai",
        }
    )

    monkeypatch.setenv("AISOC_MCP_ACTIVE", "false")

    agent_kwargs = build_profile_agent_kwargs(
        "context-123",
        platform="aisoc_web",
        config_module=config_module,
        runtime_provider_module=runtime_provider_module,
    )

    assert agent_kwargs["enabled_toolsets"] == ["hermes-cli"]


def test_build_profile_agent_kwargs_honors_no_mcp_sentinel() -> None:
    cfg = {
        "model": {
            "default": "deepseek-v4-flash",
            "provider": "custom:chatai",
        },
        "platform_toolsets": {
            "aisoc_web": ["web", "no_mcp"],
        },
        "mcp_servers": {
            "filesystem": {"enabled": True},
        },
    }
    config_module = SimpleNamespace(
        load_config_readonly=lambda: cfg,
        cfg_get=_fake_cfg_get,
    )
    runtime_provider_module = SimpleNamespace(
        resolve_runtime_provider=lambda **_: {
            "provider": "custom",
            "model": "gpt-5.4",
            "source": "custom_provider:chatai",
        }
    )

    agent_kwargs = build_profile_agent_kwargs(
        "context-123",
        platform="aisoc_web",
        config_module=config_module,
        runtime_provider_module=runtime_provider_module,
    )

    assert agent_kwargs["enabled_toolsets"] == ["web"]


def test_build_profile_agent_kwargs_preserves_explicit_mcp_allowlist() -> None:
    cfg = {
        "model": {
            "default": "deepseek-v4-flash",
            "provider": "custom:chatai",
        },
        "platform_toolsets": {
            "aisoc_web": ["web", "github"],
        },
        "mcp_servers": {
            "filesystem": {"enabled": True},
            "github": {"enabled": True},
        },
    }
    config_module = SimpleNamespace(
        load_config_readonly=lambda: cfg,
        cfg_get=_fake_cfg_get,
    )
    runtime_provider_module = SimpleNamespace(
        resolve_runtime_provider=lambda **_: {
            "provider": "custom",
            "model": "gpt-5.4",
            "source": "custom_provider:chatai",
        }
    )

    agent_kwargs = build_profile_agent_kwargs(
        "context-123",
        platform="aisoc_web",
        config_module=config_module,
        runtime_provider_module=runtime_provider_module,
    )

    assert agent_kwargs["enabled_toolsets"] == ["web", "github"]


def test_default_agent_factory_forwards_ephemeral_system_prompt(
    monkeypatch,
) -> None:
    cfg = {
        "model": {
            "default": "deepseek-v4-flash",
            "provider": "custom:chatai",
        },
    }
    config_module = SimpleNamespace(
        load_config_readonly=lambda: cfg,
        cfg_get=_fake_cfg_get,
    )
    runtime_provider_module = SimpleNamespace(
        resolve_runtime_provider=lambda **_: {
            "provider": "custom",
            "model": "gpt-5.4",
            "source": "custom_provider:chatai",
        }
    )
    created_kwargs: dict[str, object] = {}

    class _FakeAgent:
        def __init__(self, **kwargs):
            created_kwargs.update(kwargs)

    class _FakeSessionDB:
        pass

    monkeypatch.setattr("run_agent.AIAgent", _FakeAgent)

    agent = default_agent_factory(
        "context-123",
        platform="aisoc_web",
        config_module=config_module,
        runtime_provider_module=runtime_provider_module,
        session_db_cls=_FakeSessionDB,
        ephemeral_system_prompt="Stay in incident-response mode.",
    )

    assert isinstance(agent, _FakeAgent)
    assert created_kwargs["session_id"] == "context-123"
    assert created_kwargs["ephemeral_system_prompt"] == "Stay in incident-response mode."
