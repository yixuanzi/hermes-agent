"""Memory routes for AISOC backend."""

from __future__ import annotations

from fastapi import APIRouter

from aisoc.backend.models import MemoryWriteRequest
from aisoc.backend.services import memory_service


def build_memory_router() -> APIRouter:
    router = APIRouter(prefix="/api/memory", tags=["memory"])

    @router.get("")
    async def memory_index():
        return memory_service.list_memory_bundle()

    @router.get("/soul")
    async def read_soul():
        return memory_service.read_soul()

    @router.put("/soul")
    async def write_soul(body: MemoryWriteRequest):
        return memory_service.write_soul(body.content)

    @router.get("/user")
    async def read_user_preferences():
        return memory_service.read_user_preferences()

    @router.put("/user")
    async def write_user_preferences(body: MemoryWriteRequest):
        return memory_service.write_user_preferences(body.content)

    @router.get("/files/{name}")
    async def read_memory_file(name: str):
        return memory_service.read_memory_file(name)

    @router.put("/files/{name}")
    async def write_memory_file(name: str, body: MemoryWriteRequest):
        return memory_service.write_memory_file(name, body.content)

    return router

