"""Overview routes for AISOC backend."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request

from aisoc.backend.auth import require_authenticated_user
from aisoc.backend.config import AisocSettings
from aisoc.backend.services import overview_service
from aisoc.backend.services.user_service import UserService


def build_overview_router(settings: AisocSettings, user_service: UserService) -> APIRouter:
    router = APIRouter(prefix="/api/overview", tags=["overview"])

    def _user_id(request: Request) -> str:
        user, _payload = require_authenticated_user(request, settings, user_service)
        return user.uid

    @router.get("/status")
    async def status():
        return overview_service.get_status()

    @router.get("/stats")
    async def stats():
        return overview_service.get_stats()

    @router.get("/token-trend")
    async def token_trend(days: int = 7):
        if days not in {7, 30}:
            raise HTTPException(status_code=422, detail="days must be 7 or 30")
        return overview_service.get_token_trend(days=days)

    @router.get("/security-events")
    async def security_events(request: Request, limit: int = 15):
        return overview_service.list_security_events(limit=limit, user_id=_user_id(request))

    @router.get("/keywords")
    async def keywords(request: Request):
        return overview_service.list_keywords(user_id=_user_id(request))

    @router.get("/keywords/{keyword}/sessions")
    async def keyword_sessions(request: Request, keyword: str):
        return overview_service.list_keyword_sessions(keyword=keyword, user_id=_user_id(request))

    @router.get("/cron-token-dist")
    async def cron_token_dist(period: Literal["today", "7d", "30d"] = "today"):
        return overview_service.get_cron_token_distribution(period=period)

    @router.get("/cronjobs")
    async def cronjobs():
        return overview_service.get_cronjobs()

    @router.get("/model-usage")
    async def model_usage(period: Literal["today", "7d", "30d"] = "7d"):
        return overview_service.get_model_usage(period=period)

    return router
