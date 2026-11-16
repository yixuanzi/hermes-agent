"""Session routes for AISOC backend."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from aisoc.backend.auth import require_authenticated_user
from aisoc.backend.config import AisocSettings
from aisoc.backend.services import session_service
from aisoc.backend.services.user_service import UserService


def build_sessions_router(settings: AisocSettings, user_service: UserService) -> APIRouter:
    router = APIRouter(prefix="/api/sessions", tags=["sessions"])

    def _current_user(request: Request):
        user, _payload = require_authenticated_user(request, settings, user_service)
        return user

    @router.get("")
    async def get_sessions(
        request: Request,
        limit: int = 20,
        offset: int = 0,
        source: str = "",
        mine: bool = False,
    ):
        user = _current_user(request)
        return session_service.list_sessions(
            limit=limit,
            offset=offset,
            source=source or None,
            user_id=user.uid,
            strict_owner=mine,
        )

    @router.get("/search")
    async def search_sessions(request: Request, q: str = "", limit: int = 20):
        user = _current_user(request)
        return session_service.search_sessions(query=q, limit=limit, user_id=user.uid)

    @router.get("/{session_id}")
    async def session_summary(session_id: str, request: Request):
        user = _current_user(request)
        session = session_service.get_session_detail(session_id, user_id=user.uid)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return session

    @router.get("/{session_id}/detail")
    async def session_detail(session_id: str, request: Request):
        user = _current_user(request)
        payload = session_service.get_session_detail_with_messages(
            session_id, user_id=user.uid
        )
        if not payload:
            raise HTTPException(status_code=404, detail="Session not found")
        return payload

    @router.get("/{session_id}/latest-descendant")
    async def latest_descendant(session_id: str, request: Request):
        user = _current_user(request)
        payload = session_service.get_latest_descendant(session_id, user_id=user.uid)
        if not payload:
            raise HTTPException(status_code=404, detail="Session not found")
        return payload

    @router.get("/{session_id}/messages")
    async def session_messages(session_id: str, request: Request):
        user = _current_user(request)
        payload = session_service.get_session_messages(session_id, user_id=user.uid)
        if not payload:
            raise HTTPException(status_code=404, detail="Session not found")
        return payload

    @router.delete("/{session_id}")
    async def delete_session(session_id: str, request: Request):
        user = _current_user(request)
        if not session_service.delete_session(session_id, user_id=user.uid):
            raise HTTPException(status_code=404, detail="Session not found")
        return {"ok": True}

    return router
