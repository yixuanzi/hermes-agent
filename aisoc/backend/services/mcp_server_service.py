"""MCP server management service adapters.

Thin wrapper around hermes_cli.mcp_config's config.yaml-backed MCP server
storage/probe primitives, mirroring how skill_service.py wraps
hermes_cli.skills_config. Deliberately does not reuse
hermes_cli/web_routers/mcp.py — that router is coupled to web_server.py's
module-level OAuth flow registry and profile-scope helpers via late binding.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class McpServerNotFoundError(Exception):
    """Raised when a requested MCP server name has no config.yaml entry."""


def _redact_env(env: dict[str, Any]) -> dict[str, str]:
    from hermes_cli.config import redact_key

    out: dict[str, str] = {}
    for key, value in (env or {}).items():
        try:
            out[str(key)] = redact_key(str(value)) if value else ""
        except Exception:
            out[str(key)] = "***"
    return out


def _tools_allowlist(cfg: dict[str, Any]) -> list[str] | None:
    """Read the tool allow-list, tolerating the legacy bare-list shape.

    The canonical config shape is ``tools: {"include": [...]}``. Some
    servers still have ``tools`` saved as a plain list from before
    ``update_tools`` was fixed to write the dict shape — read it the same
    way here so the page doesn't crash, and the next "保存工具选择" rewrites
    it correctly.
    """
    tools_cfg = cfg.get("tools")
    if isinstance(tools_cfg, dict):
        include = tools_cfg.get("include")
        return list(include) if isinstance(include, list) else None
    if isinstance(tools_cfg, list):
        return list(tools_cfg)
    return None


def _summarize(name: str, cfg: dict[str, Any]) -> dict[str, Any]:
    transport = "http" if cfg.get("url") else ("stdio" if cfg.get("command") else "unknown")
    auth = cfg.get("auth")
    headers = cfg.get("headers") or {}
    if not auth and isinstance(headers, dict) and any(
        str(key).lower() == "authorization" for key in headers
    ):
        auth = "header"
    return {
        "name": name,
        "transport": transport,
        "url": cfg.get("url"),
        "command": cfg.get("command"),
        "args": list(cfg.get("args") or []),
        "env": _redact_env(cfg.get("env") or {}),
        "auth": auth,
        "enabled": cfg.get("enabled", True) is not False,
        "tools": _tools_allowlist(cfg),
    }


def _get_server_config(name: str) -> dict[str, Any]:
    from hermes_cli.mcp_config import _get_mcp_servers

    cfg = _get_mcp_servers().get(name)
    if cfg is None:
        raise McpServerNotFoundError(f"MCP server '{name}' not found")
    return cfg


def list_servers() -> list[dict[str, Any]]:
    from hermes_cli.mcp_config import _get_mcp_servers

    servers = _get_mcp_servers()
    return [_summarize(server_name, cfg) for server_name, cfg in sorted(servers.items())]


def get_server(name: str) -> dict[str, Any]:
    return _summarize(name, _get_server_config(name))


def _build_server_config(
    name: str,
    payload: Any,
    *,
    existing: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a config.yaml server entry from a create/update request body.

    On update, env values left blank in the payload keep their previously
    stored value (the frontend only ever shows masked env values, so a blank
    means "unchanged" rather than "clear this secret"). A header bearer token
    left blank likewise keeps the previously persisted Authorization header.
    """
    from hermes_cli.mcp_config import _bearer_auth_headers, _save_bearer_auth_token, _strip_bearer_prefix

    url = (payload.url or "").strip()
    command = (payload.command or "").strip()
    auth = (payload.auth or "none").strip().lower()

    if bool(url) == bool(command):
        raise ValueError("必须提供且只能提供 url（HTTP/SSE 类型）或 command（stdio 类型）之一")
    if auth not in {"none", "header"}:
        raise ValueError(f"不支持的鉴权模式: {auth}")

    config: dict[str, Any] = {"enabled": bool(payload.enabled)}

    if url:
        if payload.args:
            raise ValueError("args 仅适用于 stdio 类型的 MCP 服务器")
        if payload.env:
            raise ValueError("env 仅适用于 stdio 类型的 MCP 服务器")
        config["url"] = url
        if auth == "header":
            token = (payload.bearer_token or "").strip()
            normalized = _strip_bearer_prefix(token) if token else ""
            if normalized and normalized.lower() != "bearer":
                config["headers"] = _save_bearer_auth_token(name, token)
            elif existing and existing.get("headers"):
                config["headers"] = existing["headers"]
            else:
                raise ValueError("使用 header 鉴权时必须提供 bearer_token")
    else:
        config["command"] = command
        if payload.args:
            config["args"] = list(payload.args)
        existing_env = dict((existing or {}).get("env") or {})
        merged_env: dict[str, str] = {}
        for key, value in (payload.env or {}).items():
            if value:
                merged_env[key] = value
            elif key in existing_env:
                merged_env[key] = existing_env[key]
        if merged_env:
            config["env"] = merged_env

    return config


def create_server(payload: Any) -> dict[str, Any]:
    from hermes_cli.mcp_config import _get_mcp_servers, _save_mcp_server

    name = payload.name.strip()
    if not name:
        raise ValueError("服务器名称不能为空")
    if name in _get_mcp_servers():
        raise ValueError(f"MCP 服务器 '{name}' 已存在")

    config = _build_server_config(name, payload, existing=None)
    if not _save_mcp_server(name, config):
        raise ValueError(f"服务器 '{name}' 因配置校验未通过而未保存")
    return get_server(name)


def update_server(name: str, payload: Any) -> dict[str, Any]:
    from hermes_cli.mcp_config import _save_mcp_server

    existing = _get_server_config(name)
    config = _build_server_config(name, payload, existing=existing)
    if not _save_mcp_server(name, config):
        raise ValueError(f"服务器 '{name}' 因配置校验未通过而未保存")
    return get_server(name)


def delete_server(name: str) -> None:
    from hermes_cli.mcp_config import _remove_mcp_server

    if not _remove_mcp_server(name):
        raise McpServerNotFoundError(f"MCP server '{name}' not found")


def set_enabled(name: str, enabled: bool) -> dict[str, Any]:
    from hermes_cli.mcp_config import _save_mcp_server

    config = dict(_get_server_config(name))
    config["enabled"] = bool(enabled)
    _save_mcp_server(name, config)
    return get_server(name)


def update_tools(name: str, tools: list[str] | None) -> dict[str, Any]:
    from hermes_cli.mcp_config import _save_mcp_server

    config = dict(_get_server_config(name))
    if tools:
        config["tools"] = {"include": list(tools)}
    else:
        config.pop("tools", None)
    _save_mcp_server(name, config)
    return get_server(name)


def test_server(name: str) -> dict[str, Any]:
    from hermes_cli.mcp_config import _probe_single_server

    config = _get_server_config(name)
    details: dict[str, Any] = {}
    try:
        tools = _probe_single_server(name, config, details=details)
    except Exception as exc:  # connection/probe failures are expected, not a 500
        return {"ok": False, "error": str(exc), "tools": []}
    return {
        "ok": True,
        "tools": [{"name": tool_name, "description": desc} for tool_name, desc in tools],
        "prompts": details.get("prompts", 0),
        "resources": details.get("resources", 0),
    }


def reload_servers() -> dict[str, Any]:
    from aisoc.backend.agent_runtime import start_aisoc_mcp_bootstrap

    start_aisoc_mcp_bootstrap(logger=logger)
    return {"ok": True}
