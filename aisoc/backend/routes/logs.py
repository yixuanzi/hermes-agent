"""Logs routes for AISOC backend."""

from __future__ import annotations

from fastapi import APIRouter

from aisoc.backend.services import log_service


def build_logs_router() -> APIRouter:
    router = APIRouter(prefix="/api/logs", tags=["logs"])

    @router.get("")
    async def get_logs(
        file: str = "agent",
        lines: int = 100,
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

