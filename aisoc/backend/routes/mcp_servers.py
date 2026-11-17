"""MCP server management routes for AISOC backend."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request

from aisoc.backend.auth import require_admin_user, require_authenticated_user
from aisoc.backend.config import AisocSettings
from aisoc.backend.models import (
    McpServerCreate,
    McpServerEnabledUpdate,
    McpServerToolsUpdate,
    McpServerUpdate,
)
from aisoc.backend.services import mcp_server_service
from aisoc.backend.services.user_service import UserService


def build_mcp_servers_router(settings: AisocSettings, user_service: UserService) -> APIRouter:
    """MCP servers are host-wide config every user's agent loads, and stdio
    entries run host commands: only admins change, probe or reload them, and
    other users read them with credentials masked."""
    router = APIRouter(prefix="/api/mcp-servers", tags=["mcp-servers"])

    def _is_admin(request: Request) -> bool:
        user, _payload = require_authenticated_user(request, settings, user_service)
        return user.is_admin

    def _ensure_admin(request: Request) -> None:
        user, _payload = require_authenticated_user(request, settings, user_service)
        require_admin_user(user)

    @router.get("")
    async def list_servers(request: Request):
        servers = mcp_server_service.list_servers()
        if _is_admin(request):
            return servers
        return [mcp_server_service.mask_for_reader(server) for server in servers]

    @router.get("/{name}")
    async def get_server(name: str, request: Request):
        try:
            server = mcp_server_service.get_server(name)
        except mcp_server_service.McpServerNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return server if _is_admin(request) else mcp_server_service.mask_for_reader(server)

    @router.post("")
    async def create_server(body: McpServerCreate, request: Request):
        _ensure_admin(request)
        try:
            return mcp_server_service.create_server(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.put("/{name}")
    async def update_server(name: str, body: McpServerUpdate, request: Request):
        _ensure_admin(request)
        try:
            return mcp_server_service.update_server(name, body)
        except mcp_server_service.McpServerNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/{name}")
    async def delete_server(name: str, request: Request):
        _ensure_admin(request)
        try:
            mcp_server_service.delete_server(name)
        except mcp_server_service.McpServerNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True, "name": name}

    @router.put("/{name}/enabled")
    async def set_server_enabled(name: str, body: McpServerEnabledUpdate, request: Request):
        _ensure_admin(request)
        try:
            return mcp_server_service.set_enabled(name, body.enabled)
        except mcp_server_service.McpServerNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.put("/{name}/tools")
    async def update_server_tools(name: str, body: McpServerToolsUpdate, request: Request):
        _ensure_admin(request)
        try:
            return mcp_server_service.update_tools(name, body.tools)
        except mcp_server_service.McpServerNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/{name}/test")
    async def test_server(name: str, request: Request):
        _ensure_admin(request)
        try:
            # A stdio server's cold start can take several seconds — run off
            # the event loop so it doesn't stall every other AISOC request.
            return await asyncio.to_thread(mcp_server_service.test_server, name)
        except mcp_server_service.McpServerNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/reload")
    async def reload_servers(request: Request):
        _ensure_admin(request)
        return mcp_server_service.reload_servers()

    return router
