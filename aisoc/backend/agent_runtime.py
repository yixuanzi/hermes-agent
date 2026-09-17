"""Shared Hermes runtime helpers for AISOC modules."""

from __future__ import annotations

import logging
import os

from hermes_state import SessionDB
from hermes_constants import get_config_path
from hermes_cli import config as hermes_config
from hermes_cli import runtime_provider


logger = logging.getLogger(__name__)

_AISOC_MCP_WAIT_TIMEOUT_SECONDS = 0.75


def prepare_hermes_home() -> None:
    """Point AISOC child operations at the active Hermes profile directory."""
    try:
        from hermes_constants import get_hermes_home

        print(f"Using Hermes home: {get_hermes_home()}")
        hermes_home = str(get_hermes_home())
        os.chdir(hermes_home)
        os.environ["HERMES_HOME"] = hermes_home
        os.environ["HOME"] = hermes_home + "/home"
    except Exception as exc:
        print(f"Warning: Failed to set TERMINAL_CWD from Hermes profile: {exc}")


def _default_toolsets_for_platform(platform: str) -> list[str]:
    return ["hermes-cli"]


def _parse_enabled_flag(value, default: bool = True) -> bool:
    """Parse bool-like config values used by AISOC MCP settings."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return default


def _parse_aisoc_mcp_active() -> bool | None:
    """Return the AISOC MCP env override: True / False / None (auto)."""
    raw = os.environ.get("AISOC_MCP_ACTIVE")
    if raw is None or not str(raw).strip():
        return None
    return _parse_enabled_flag(raw, default=True)


def _get_enabled_mcp_servers(cfg: dict[str, object]) -> list[str]:
    mcp_servers = cfg.get("mcp_servers") or {}
    if not isinstance(mcp_servers, dict):
        return []

    enabled_servers: list[str] = []
    for raw_name, server_cfg in mcp_servers.items():
        name = str(raw_name).strip()
        if not name:
            continue
        if isinstance(server_cfg, dict):
            enabled = _parse_enabled_flag(server_cfg.get("enabled", True), default=True)
        else:
            enabled = True
        if enabled:
            enabled_servers.append(name)
    return enabled_servers


def _merge_aisoc_mcp_toolsets(
    configured_toolsets: list[str],
    cfg: dict[str, object],
) -> list[str]:
    """Merge configured toolsets with AISOC MCP server toolsets."""
    normalized_toolsets = [toolset for toolset in configured_toolsets if toolset != "no_mcp"]

    mcp_active = _parse_aisoc_mcp_active()
    if mcp_active is False:
        return normalized_toolsets

    enabled_mcp_servers = _get_enabled_mcp_servers(cfg)
    if not enabled_mcp_servers:
        return normalized_toolsets

    if "no_mcp" in configured_toolsets:
        return normalized_toolsets

    explicit_mcp_servers = [
        toolset for toolset in normalized_toolsets if toolset in enabled_mcp_servers
    ]
    if explicit_mcp_servers:
        return normalized_toolsets

    merged_toolsets = list(normalized_toolsets)
    for server_name in enabled_mcp_servers:
        if server_name not in merged_toolsets:
            merged_toolsets.append(server_name)
    return merged_toolsets


def start_aisoc_mcp_bootstrap(*, logger: logging.Logger | None = None) -> None:
    """Start Hermes-style background MCP discovery for AISOC agent entrypoints."""
    if _parse_aisoc_mcp_active() is False:
        return

    from hermes_cli.mcp_startup import start_background_mcp_discovery

    start_background_mcp_discovery(
        logger=logger or globals()["logger"],
        thread_name="aisoc-mcp-discovery",
    )


def wait_for_aisoc_mcp_bootstrap(timeout: float = _AISOC_MCP_WAIT_TIMEOUT_SECONDS) -> None:
    """Bounded wait for AISOC MCP discovery before first agent construction."""
    if _parse_aisoc_mcp_active() is False:
        return

    from hermes_cli.mcp_startup import wait_for_mcp_discovery

    wait_for_mcp_discovery(timeout=timeout)


def _resolve_profile_enabled_toolsets(
    cfg: dict[str, object],
    *,
    platform: str,
    config_module=hermes_config,
) -> list[str]:
    """Read per-platform toolsets from the already-loaded profile config."""
    configured_toolsets = config_module.cfg_get(cfg, "platform_toolsets", platform, default=None)
    if configured_toolsets is None:
        normalized_toolsets = _default_toolsets_for_platform(platform)
        return _merge_aisoc_mcp_toolsets(normalized_toolsets, cfg)
    if not isinstance(configured_toolsets, list):
        normalized_toolsets = _default_toolsets_for_platform(platform)
        return _merge_aisoc_mcp_toolsets(normalized_toolsets, cfg)

    normalized_toolsets: list[str] = []
    seen_toolsets: set[str] = set()
    for raw_toolset in configured_toolsets:
        toolset_name = str(raw_toolset).strip()
        if not toolset_name or toolset_name in seen_toolsets:
            continue
        normalized_toolsets.append(toolset_name)
        seen_toolsets.add(toolset_name)
    return _merge_aisoc_mcp_toolsets(normalized_toolsets, cfg)


def build_profile_agent_kwargs(
    session_id: str,
    *,
    platform: str,
    config_module=hermes_config,
    runtime_provider_module=runtime_provider,
) -> dict[str, object]:
    """Resolve the current profile into explicit AIAgent kwargs."""
    cfg = config_module.load_config_readonly()
    model_cfg = config_module.cfg_get(cfg, "model", default={})
    if not isinstance(model_cfg, dict):
        model_cfg = {}

    requested_provider = str(model_cfg.get("provider") or "").strip() or None
    requested_model = str(model_cfg.get("default") or model_cfg.get("model") or "").strip() or None

    runtime = runtime_provider_module.resolve_runtime_provider(
        requested=requested_provider,
        target_model=requested_model,
    )

    resolved_provider = str(runtime.get("provider") or requested_provider or "auto").strip()

    # A named custom provider entry may carry its own baked-in default_model,
    # which resolve_runtime_provider() returns via runtime["model"] so that
    # e.g. `hermes chat --model <provider-name>` doesn't send the provider
    # alias to the API as a model string. Only fall back to that baked-in
    # model when the profile didn't already request one (or requested the
    # provider's own name/slug) — otherwise it silently overrides an
    # explicit model.default from config.yaml. Mirrors the equivalent guard
    # in hermes_cli/cli_agent_setup_mixin.py.
    runtime_model = str(runtime.get("model") or "").strip()
    should_use_runtime_model = runtime_model and (
        not requested_model
        or requested_model == resolved_provider
        or requested_model == str(runtime.get("name") or "").strip()
    )
    resolved_model = runtime_model if should_use_runtime_model else (requested_model or "")
    resolved_base_url = str(runtime.get("base_url") or "").strip()
    resolved_api_key = str(runtime.get("api_key") or "").strip()
    resolved_api_mode = str(runtime.get("api_mode") or "").strip()

    agent_kwargs: dict[str, object] = {
        "quiet_mode": True,
        "platform": platform,
        "session_id": session_id,
        "provider": resolved_provider,
        "enabled_toolsets": _resolve_profile_enabled_toolsets(
            cfg,
            platform=platform,
            config_module=config_module,
        ),
    }
    if resolved_model:
        agent_kwargs["model"] = resolved_model
    if resolved_base_url:
        agent_kwargs["base_url"] = resolved_base_url
    if resolved_api_key:
        agent_kwargs["api_key"] = resolved_api_key
    if resolved_api_mode:
        agent_kwargs["api_mode"] = resolved_api_mode
    if runtime.get("request_overrides"):
        agent_kwargs["request_overrides"] = dict(runtime["request_overrides"])
    if runtime.get("fallback_model"):
        agent_kwargs["fallback_model"] = runtime["fallback_model"]

    return agent_kwargs


def default_agent_factory(
    session_id: str,
    *,
    platform: str,
    ephemeral_system_prompt: str | None = None,
    user_id: str | None = None,
    user_name: str | None = None,
    skip_memory: bool = False,
    disabled_toolsets: list[str] | None = None,
    config_module=hermes_config,
    runtime_provider_module=runtime_provider,
    session_db_cls=SessionDB,
    log: logging.Logger | None = None,
):
    """Create a profile-configured AIAgent for AISOC modules."""
    active_logger = log or logger
    start_aisoc_mcp_bootstrap(logger=active_logger)
    wait_for_aisoc_mcp_bootstrap()

    from run_agent import AIAgent

    agent_kwargs = build_profile_agent_kwargs(
        session_id,
        platform=platform,
        config_module=config_module,
        runtime_provider_module=runtime_provider_module,
    )
    if ephemeral_system_prompt is not None:
        agent_kwargs["ephemeral_system_prompt"] = ephemeral_system_prompt
    if user_id:
        agent_kwargs["user_id"] = str(user_id)
    if user_name:
        agent_kwargs["user_name"] = str(user_name)
    if skip_memory:
        agent_kwargs["skip_memory"] = True
    if disabled_toolsets:
        agent_kwargs["disabled_toolsets"] = list(disabled_toolsets)
    active_logger.info(
        "%s profile injection from %s: %s",
        "AISOC",
        get_config_path(),
        {
            "provider": agent_kwargs.get("provider"),
            "model": agent_kwargs.get("model"),
            "base_url": agent_kwargs.get("base_url"),
            "api_mode": agent_kwargs.get("api_mode"),
        },
    )
    try:
        agent_kwargs["session_db"] = session_db_cls()
    except Exception as exc:
        active_logger.warning(
            "AISOC SessionDB unavailable; continuing without session persistence: %s",
            exc,
        )
    return AIAgent(**agent_kwargs)


def load_conversation_history(
    agent: object,
    session_id: str | None = None,
) -> list[dict[str, object]]:
    """Load OpenAI-format conversation history from the agent's SessionDB."""
    active_session_id = str(session_id or getattr(agent, "session_id", "") or "").strip()
    if not active_session_id:
        return []

    session_db = None
    getter = getattr(agent, "_get_session_db_for_recall", None)
    if callable(getter):
        try:
            session_db = getter()
        except Exception:
            session_db = None
    if session_db is None:
        session_db = getattr(agent, "_session_db", None)
    if session_db is None:
        return []

    loader = getattr(session_db, "get_messages_as_conversation", None)
    if not callable(loader):
        return []
    try:
        history = loader(active_session_id)
    except Exception:
        return []
    return list(history or [])
