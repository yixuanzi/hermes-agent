"""Skills routes for WORKAGENT backend."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from workagent.backend.models import SkillCategoryToggleRequest, SkillToggleRequest
from workagent.backend.services import skill_service


def build_skills_router() -> APIRouter:
    router = APIRouter(prefix="/api/skills", tags=["skills"])

    @router.get("")
    async def get_skills():
        return skill_service.list_skills()

    @router.get("/{skill_name}")
    async def get_skill_detail(skill_name: str):
        try:
            return skill_service.get_skill_detail(skill_name)
        except skill_service.SkillNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.delete("/{skill_name}")
    async def delete_skill(skill_name: str):
        try:
            return skill_service.delete_skill(skill_name)
        except skill_service.SkillNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except skill_service.SkillDeleteForbiddenError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except skill_service.SkillDeleteFailedError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/{skill_name}/appendix")
    async def get_skill_appendix(skill_name: str, path: str = Query(default="", alias="path")):
        try:
            return skill_service.get_skill_appendix_content(skill_name, path)
        except skill_service.SkillNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.put("/toggle")
    async def toggle_skill(body: SkillToggleRequest):
        return skill_service.toggle_skill(body.name, body.enabled)

    @router.put("/toggle-category")
    async def toggle_category(body: SkillCategoryToggleRequest):
        try:
            return skill_service.toggle_category(body.category, body.enabled)
        except skill_service.SkillNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/reload")
    async def reload_skills():
        return {"ok": True, "reloaded": skill_service.reload_index()}

    return router
