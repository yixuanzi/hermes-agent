"""Knowledge base routes for WORKAGENT backend."""

from __future__ import annotations

from fastapi import APIRouter, Query

from workagent.backend.models import KbDocumentWriteRequest
from workagent.backend.services import kb_service


def build_kb_router() -> APIRouter:
    router = APIRouter(prefix="/api/kb", tags=["kb"])

    @router.get("/tree")
    async def tree(cwd: str = Query(default="", alias="cwd", description="Subdirectory relative to wiki root")):
        return kb_service.list_tree(cwd)

    @router.get("/documents")
    async def documents(path: str = Query(default="", alias="path", description="File path relative to wiki root")):
        return kb_service.read_document(path)

    @router.put("/documents")
    async def write_document(body: KbDocumentWriteRequest):
        return kb_service.write_document(body.path, body.content)

    return router
