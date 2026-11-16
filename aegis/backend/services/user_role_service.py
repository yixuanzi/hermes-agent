"""Business rules for administrator-managed user roles."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException

from aegis.backend.models import UserRoleRequest, UserRoleResponse
from aegis.backend.services.user_role_store import UserRoleStore


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class UserRoleService:
    def __init__(self, store: UserRoleStore) -> None:
        self._store = store

    def list_roles(self) -> list[UserRoleResponse]:
        return [UserRoleResponse.model_validate(row) for row in self._store.list()]

    def create_role(self, body: UserRoleRequest) -> UserRoleResponse:
        record = {
            "id": self._generate_id(),
            "platform": body.platform,
            "uid": body.uid,
            "uname": body.uname,
            "role": body.role,
            "update_time": _utc_timestamp(),
        }
        try:
            self._store.create(record)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail="A role already exists for this platform and UID.",
            ) from exc
        return UserRoleResponse.model_validate(record)

    def update_role(self, role_id: str, body: UserRoleRequest) -> UserRoleResponse:
        record = {
            "platform": body.platform,
            "uid": body.uid,
            "uname": body.uname,
            "role": body.role,
            "update_time": _utc_timestamp(),
        }
        try:
            updated = self._store.update(role_id, record)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail="A role already exists for this platform and UID.",
            ) from exc
        if not updated:
            raise HTTPException(status_code=404, detail="User role not found.")
        saved = self._store.get(role_id)
        if saved is None:  # pragma: no cover - protects against concurrent deletion.
            raise HTTPException(status_code=404, detail="User role not found.")
        return UserRoleResponse.model_validate(saved)

    def delete_role(self, role_id: str) -> None:
        if not self._store.delete(role_id):
            raise HTTPException(status_code=404, detail="User role not found.")

    def _generate_id(self) -> str:
        while True:
            candidate = str(uuid.uuid4())
            if self._store.get(candidate) is None:
                return candidate
