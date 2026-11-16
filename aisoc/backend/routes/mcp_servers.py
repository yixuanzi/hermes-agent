"""MCP server management routes for AISOC backend."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from aisoc.backend.models import (
    McpServerCreate,
    McpServerEnabledUpdate,
    McpServerToolsUpdate,
    McpServerUpdate,
)
from aisoc.backend.services import mcp_server_service


def build_mcp_servers_router() -> APIRouter:
    router = APIRouter(prefix="/api/mcp-servers", tags=["mcp-servers"])

    @router.get("")
    async def list_servers():
        return mcp_server_service.list_servers()

    @router.get("/{name}")
    async def get_server(name: str):
        try:
            return mcp_server_service.get_server(name)
        except mcp_server_service.McpServerNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("")
    async def create_server(body: McpServerCreate):
        try:
            return mcp_server_service.create_server(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.put("/{name}")
    async def update_server(name: str, body: McpServerUpdate):
        try:
            return mcp_server_service.update_server(name, body)
        except mcp_server_service.McpServerNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/{name}")
    async def delete_server(name: str):
        try:
            mcp_server_service.delete_server(name)
        except mcp_server_service.McpServerNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True, "name": name}

    @router.put("/{name}/enabled")
    async def set_server_enabled(name: str, body: McpServerEnabledUpdate):
        try:
            return mcp_server_service.set_enabled(name, body.enabled)
        except mcp_server_service.McpServerNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.put("/{name}/tools")
    async def update_server_tools(name: str, body: McpServerToolsUpdate):
        try:
            return mcp_server_service.update_tools(name, body.tools)
        except mcp_server_service.McpServerNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/{name}/test")
    async def test_server(name: str):
        try:
            # A stdio server's cold start can take several seconds — run off
            # the event loop so it doesn't stall every other AISOC request.
            return await asyncio.to_thread(mcp_server_service.test_server, name)
        except mcp_server_service.McpServerNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/reload")
    async def reload_servers():
        return mcp_server_service.reload_servers()

    return router
