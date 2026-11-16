"""Session routes for WORKAGENT backend."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from workagent.backend.services import session_service


def build_sessions_router() -> APIRouter:
    router = APIRouter(prefix="/api/sessions", tags=["sessions"])

    @router.get("")
    async def get_sessions(limit: int = 20, offset: int = 0):
        return session_service.list_sessions(limit=limit, offset=offset)

    @router.get("/search")
    async def search_sessions(q: str = "", limit: int = 20):
        return session_service.search_sessions(query=q, limit=limit)

    @router.get("/{session_id}")
    async def session_summary(session_id: str):
        session = session_service.get_session_detail(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return session

    @router.get("/{session_id}/detail")
    async def session_detail(session_id: str):
        payload = session_service.get_session_detail_with_messages(session_id)
        if not payload:
            raise HTTPException(status_code=404, detail="Session not found")
        return payload

    @router.get("/{session_id}/latest-descendant")
    async def latest_descendant(session_id: str):
        payload = session_service.get_latest_descendant(session_id)
        if not payload:
            raise HTTPException(status_code=404, detail="Session not found")
        return payload

    @router.get("/{session_id}/messages")
    async def session_messages(session_id: str):
        payload = session_service.get_session_messages(session_id)
        if not payload:
            raise HTTPException(status_code=404, detail="Session not found")
        return payload

    @router.delete("/{session_id}")
    async def delete_session(session_id: str):
        if not session_service.delete_session(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return {"ok": True}

    return router
