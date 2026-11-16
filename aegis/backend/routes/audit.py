"""Read-only A2A delegate audit routes for Aegis administrators."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Query, Request

from aegis.backend.auth import require_admin_user, require_authenticated_user
from aegis.backend.config import AegisSettings
from aegis.backend.models import DelegateAuditListResponse, TaskAuditListResponse
from aegis.backend.services.delegate_security_service import DelegateSecurityService
from aegis.backend.services.user_service import UserService


def _utc_filter(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def build_audit_router(
    settings: AegisSettings,
    user_service: UserService,
    service: DelegateSecurityService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/audit", tags=["audit"])

    def _service() -> DelegateSecurityService:
        return service or DelegateSecurityService()

    def _ensure_admin(request: Request) -> None:
        user, _payload = require_authenticated_user(request, settings, user_service)
        require_admin_user(user)

    @router.get("/a2a-delegates", response_model=DelegateAuditListResponse)
    async def list_a2a_delegate_audits(
        request: Request,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=100),
        id: str | None = None,
        platform: str | None = None,
        user_id: str | None = None,
        user_name: str | None = None,
        agent_name: str | None = None,
        goal: str | None = None,
        session_id: str | None = None,
        status: Literal["succ", "fail", "auth_denied"] | None = None,
        is_loop: bool | None = None,
        is_delegate_output: bool | None = None,
        timestamp_from: datetime | None = None,
        timestamp_to: datetime | None = None,
    ) -> DelegateAuditListResponse:
        _ensure_admin(request)
        return _service().query_audits(
            page=page,
            page_size=page_size,
            id=id,
            platform=platform,
            user_id=user_id,
            user_name=user_name,
            agent_name=agent_name,
            goal=goal,
            session_id=session_id,
            status=status,
            is_loop=is_loop,
            is_delegate_output=is_delegate_output,
            timestamp_from=_utc_filter(timestamp_from),
            timestamp_to=_utc_filter(timestamp_to),
        )

    @router.get("/tasks", response_model=TaskAuditListResponse)
    async def list_task_audits(
        request: Request,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=100),
        id: str | None = None,
        uid: str | None = None,
        uname: str | None = None,
        session_id: str | None = None,
        prompt: str | None = None,
        create_time_from: datetime | None = None,
        create_time_to: datetime | None = None,
    ) -> TaskAuditListResponse:
        _ensure_admin(request)
        return _service().query_task_audits(
            page=page,
            page_size=page_size,
            id=id,
            uid=uid,
            uname=uname,
            session_id=session_id,
            prompt=prompt,
            create_time_from=_utc_filter(create_time_from),
            create_time_to=_utc_filter(create_time_to),
        )

    return router
