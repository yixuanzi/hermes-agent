"""Logs routes for AISOC backend."""

from __future__ import annotations

from fastapi import APIRouter, Query

from aisoc.backend.services import log_service


def build_logs_router() -> APIRouter:
    router = APIRouter(prefix="/api/logs", tags=["logs"])

    @router.get("")
    async def get_logs(
        file: str = "agent",
        # The service caps at 500; below 1, the tail slice would return the
        # whole file (0) or all but the last N lines (-N).
        lines: int = Query(100, ge=1),
        level: str | None = None,
        component: str | None = None,
        search: str | None = None,
    ):
        return log_service.read_logs(
            file=file,
            lines=lines,
            level=level,
            component=component,
            search=search,
        )

    return router

