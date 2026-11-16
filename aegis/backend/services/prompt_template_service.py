"""Business rules for the authenticated user's prompt templates."""

from __future__ import annotations

import secrets
import sqlite3
from datetime import UTC, datetime

from fastapi import HTTPException

from aegis.backend.models import PromptTemplateRequest, PromptTemplateResponse
from aegis.backend.services.prompt_template_store import PromptTemplateStore


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class PromptTemplateService:
    def __init__(self, store: PromptTemplateStore) -> None:
        self._store = store

    def list_templates(self, user_id: str) -> list[PromptTemplateResponse]:
        return [PromptTemplateResponse.model_validate(row) for row in self._store.list_for_user(user_id)]

    def create_template(self, user_id: str, body: PromptTemplateRequest) -> PromptTemplateResponse:
        now = _utc_timestamp()
        record = {
            "id": self._generate_id(user_id),
            "user_id": user_id,
            "tag": body.tag,
            "desc": body.desc,
            "prompt": body.prompt,
            "create_time": now,
            "update_time": now,
        }
        try:
            self._store.create(record)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Prompt template already exists.") from exc
        return self._to_response(record)

    def update_template(
        self, template_id: str, user_id: str, body: PromptTemplateRequest,
    ) -> PromptTemplateResponse:
        record = {
            "tag": body.tag,
            "desc": body.desc,
            "prompt": body.prompt,
            "update_time": _utc_timestamp(),
        }
        if not self._store.update_for_user(template_id, user_id, record):
            raise HTTPException(status_code=404, detail="Prompt template not found.")
        updated = self._store.get_for_user(template_id, user_id)
        if updated is None:  # pragma: no cover - protects against concurrent deletion.
            raise HTTPException(status_code=404, detail="Prompt template not found.")
        return PromptTemplateResponse.model_validate(updated)

    def delete_template(self, template_id: str, user_id: str) -> None:
        if not self._store.delete_for_user(template_id, user_id):
            raise HTTPException(status_code=404, detail="Prompt template not found.")

    def _generate_id(self, user_id: str) -> str:
        while True:
            candidate = secrets.token_hex(12)
            if self._store.get_for_user(candidate, user_id) is None:
                return candidate

    @staticmethod
    def _to_response(record: dict) -> PromptTemplateResponse:
        return PromptTemplateResponse.model_validate(
            {key: record[key] for key in ("id", "tag", "desc", "prompt", "create_time", "update_time")}
        )
