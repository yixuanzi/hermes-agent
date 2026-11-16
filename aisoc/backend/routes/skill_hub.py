"""SkillHub integration routes for AISOC's Integrator module.

Separate from ``routes/skills.py`` (the pre-existing Skills nav page, which
lists/toggles/browses the full local skill directory and is left untouched).
This router backs the new Integrator > SkillHub card view: list installed
hub skills, install/sync/uninstall — status sync only, content lives in
SkillHub.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from aisoc.backend.models import SkillHubInstallRequest
from aisoc.backend.services import skill_hub_service


def build_skill_hub_router() -> APIRouter:
    router = APIRouter(prefix="/api/skill-hub", tags=["skill-hub"])

    @router.get("/status")
    async def get_status():
        return skill_hub_service.hub_status()

    @router.get("/skills")
    async def list_skills():
        return skill_hub_service.list_installed_skills()

    @router.get("/catalog")
    async def get_catalog(search: str = ""):
        # Blocking httpx.get — run off the event loop (same reasoning as
        # mcp_servers.test_server: a slow/unreachable hub shouldn't stall
        # every other AISOC request).
        return await asyncio.to_thread(skill_hub_service.list_catalog, search)

    @router.post("/skills")
    async def install_skill(body: SkillHubInstallRequest):
        try:
            # 下载 + 隔离安全扫描可能耗时数秒到分钟级，放线程池执行，
            # 避免阻塞事件循环（同 mcp_servers 的 test_server 做法）。
            return await asyncio.to_thread(skill_hub_service.install_skill, body.identifier)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except skill_hub_service.SkillHubError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post("/skills/{name}/sync")
    async def sync_skill(name: str):
        try:
            return await asyncio.to_thread(skill_hub_service.sync_skill, name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except skill_hub_service.SkillHubError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.delete("/skills/{name}")
    async def uninstall_skill(name: str):
        try:
            return await asyncio.to_thread(skill_hub_service.uninstall_skill, name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except skill_hub_service.SkillHubError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
