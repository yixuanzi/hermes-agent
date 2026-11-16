"""Business rules for global Aegis system instructions."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from fastapi import HTTPException

from aegis.backend.models import SystemInstructRequest, SystemInstructResponse
from aegis.backend.services.system_instruct_store import SystemInstructStore


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class SystemInstructService:
    def __init__(self, store: SystemInstructStore) -> None:
        self._store = store

    def list_instructions(self) -> list[SystemInstructResponse]:
        return [SystemInstructResponse.model_validate(row) for row in self._store.list()]

    def create_instruction(self, body: SystemInstructRequest) -> SystemInstructResponse:
        now = _utc_timestamp()
        record = {
            "id": self._generate_id(),
            "name": body.name,
            "describe": body.describe,
            "instruct": body.instruct,
            "create_time": now,
            "update_time": now,
            "status": body.status,
        }
        self._store.create(record)
        return SystemInstructResponse.model_validate(record)

    def update_instruction(
        self,
        instruct_id: str,
        body: SystemInstructRequest,
    ) -> SystemInstructResponse:
        record = {
            "name": body.name,
            "describe": body.describe,
            "instruct": body.instruct,
            "update_time": _utc_timestamp(),
            "status": body.status,
        }
        if not self._store.update(instruct_id, record):
            raise HTTPException(status_code=404, detail="System instruction not found.")
        updated = self._store.get(instruct_id)
        if updated is None:  # pragma: no cover - protects against concurrent deletion.
            raise HTTPException(status_code=404, detail="System instruction not found.")
        return SystemInstructResponse.model_validate(updated)

    def delete_instruction(self, instruct_id: str) -> None:
        if not self._store.delete(instruct_id):
            raise HTTPException(status_code=404, detail="System instruction not found.")

    def _generate_id(self) -> str:
        while True:
            candidate = secrets.token_hex(12)
            if self._store.get(candidate) is None:
                return candidate
