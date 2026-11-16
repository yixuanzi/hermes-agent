"""Overview routes for WORKAGENT backend."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException

from workagent.backend.services import overview_service


def build_overview_router() -> APIRouter:
    router = APIRouter(prefix="/api/overview", tags=["overview"])

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
    async def security_events(limit: int = 15):
        return overview_service.list_security_events(limit=limit)

    @router.get("/keywords")
    async def keywords():
        return overview_service.list_keywords()

    @router.get("/keywords/{keyword}/sessions")
    async def keyword_sessions(keyword: str):
        return overview_service.list_keyword_sessions(keyword=keyword)

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
