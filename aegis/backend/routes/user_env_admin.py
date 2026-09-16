"""Authenticated routes for a user's own aegis-platform env variables.

Every route derives the caller's identity from the bearer-token session —
the partition key (``aegis.{user_id}``) is computed server-side and never
accepted from the client, so one user can never touch another's partition.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from aegis.backend.auth import require_authenticated_user
from aegis.backend.config import AegisSettings
from aegis.backend.models import (
    UserEnvDeleteResponse,
    UserEnvEntry,
    UserEnvListResponse,
    UserEnvSetRequest,
    UserEnvSetResponse,
)
from aegis.backend.services.user_env_admin_store import UserEnvAdminError, UserEnvAdminStore


def build_user_env_router(settings: AegisSettings, user_service, store: UserEnvAdminStore) -> APIRouter:
    router = APIRouter(prefix="/api/my/user-env", tags=["user-env"])

    def _user(request: Request):
        return require_authenticated_user(request, settings, user_service)

    @router.get("", response_model=UserEnvListResponse)
    async def list_vars(request: Request) -> UserEnvListResponse:
        user, _payload = _user(request)
        env = store.list_for_user(user.uid)
        return UserEnvListResponse(
            variables=[UserEnvEntry(key=k, masked_value=v) for k, v in sorted(env.items())]
        )

    @router.put("", response_model=UserEnvSetResponse)
    async def set_var(body: UserEnvSetRequest, request: Request) -> UserEnvSetResponse:
        user, _payload = _user(request)
        try:
            env = store.set_var(user.uid, user.username, body.key, body.value)
        except UserEnvAdminError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return UserEnvSetResponse(
            key=body.key,
            variables=[UserEnvEntry(key=k, masked_value=v) for k, v in sorted(env.items())],
        )

    @router.delete("/{env_key}", response_model=UserEnvDeleteResponse)
    async def delete_var(env_key: str, request: Request) -> UserEnvDeleteResponse:
        user, _payload = _user(request)
        try:
            deleted = store.delete_var(user.uid, env_key)
        except UserEnvAdminError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Key not found: {env_key}")
        return UserEnvDeleteResponse(deleted=True, key=env_key)

    return router
