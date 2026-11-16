from __future__ import annotations

import secrets
import string
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from aegis.backend.models import (
    GlobalRoutingRuleResponse,
    GlobalRoutingRuleUpsertRequest,
)
from aegis.backend.services.store import AegisStore, get_aegis_store


_RULE_ID_ALPHABET = string.ascii_lowercase + string.digits
_RULE_ID_LENGTH = 8
_MAX_ID_GENERATION_ATTEMPTS = 128


class RoutingService:
    def __init__(self, store: AegisStore | None = None) -> None:
        self._store = store or get_aegis_store()

    def list_global_rules(self) -> list[GlobalRoutingRuleResponse]:
        payload = self._store.read_locked()
        return self._validate_rules(payload["global"])

    def get_global_rule(self, rule_id: str) -> GlobalRoutingRuleResponse:
        payload = self._store.read_locked()
        _, rule = self._find_rule(payload["global"], rule_id)
        return rule

    def create_global_rule(
        self,
        body: GlobalRoutingRuleUpsertRequest,
    ) -> GlobalRoutingRuleResponse:
        def _mutate(payload: dict[str, Any]) -> GlobalRoutingRuleResponse:
            self._validate_rules(payload["global"])
            rule_id = self._generate_rule_id(payload["global"])
            stored_rule = {"id": rule_id, **body.model_dump(mode="json")}
            payload["global"].append(stored_rule)
            return self._build_rule_response(stored_rule)

        return self._store.mutate_locked(_mutate)

    def update_global_rule(
        self,
        rule_id: str,
        body: GlobalRoutingRuleUpsertRequest,
    ) -> GlobalRoutingRuleResponse:
        def _mutate(payload: dict[str, Any]) -> GlobalRoutingRuleResponse:
            index, _ = self._find_rule(payload["global"], rule_id)
            updated_rule = {"id": rule_id, **body.model_dump(mode="json")}
            payload["global"][index] = updated_rule
            return self._build_rule_response(updated_rule)

        return self._store.mutate_locked(_mutate)

    def delete_global_rule(self, rule_id: str) -> None:
        def _mutate(payload: dict[str, Any]) -> None:
            index, _ = self._find_rule(payload["global"], rule_id)
            del payload["global"][index]

        self._store.mutate_locked(_mutate)

    def _generate_rule_id(self, rules: list[Any]) -> str:
        existing_ids = {rule.id for rule in self._validate_rules(rules)}

        for _ in range(_MAX_ID_GENERATION_ATTEMPTS):
            candidate = "".join(
                secrets.choice(_RULE_ID_ALPHABET) for _ in range(_RULE_ID_LENGTH)
            )
            if candidate not in existing_ids:
                return candidate

        raise HTTPException(
            status_code=500,
            detail="Unable to generate a unique global routing rule ID.",
        )

    def _find_rule(
        self,
        rules: list[Any],
        rule_id: str,
    ) -> tuple[int, GlobalRoutingRuleResponse]:
        raw_matches = [
            (index, rule)
            for index, rule in enumerate(rules)
            if isinstance(rule, Mapping) and rule.get("id") == rule_id
        ]
        if len(raw_matches) > 1:
            raise HTTPException(
                status_code=500,
                detail=f"Stored global routing rule ID '{rule_id}' is duplicated.",
            )
        if not raw_matches:
            self._validate_rules(rules)
            raise HTTPException(
                status_code=404,
                detail=f"Global routing rule '{rule_id}' not found.",
            )

        index, raw_rule = raw_matches[0]
        validated_rule = self._build_rule_response(
            raw_rule,
            invalid_detail=f"Stored global routing rule '{rule_id}' has an invalid shape.",
        )
        self._validate_rules(rules)
        return index, validated_rule

    def _validate_rules(self, rules: list[Any]) -> list[GlobalRoutingRuleResponse]:
        validated_rules = [self._build_rule_response(rule) for rule in rules]
        seen_ids: set[str] = set()
        duplicate_ids: set[str] = set()
        for rule in validated_rules:
            if rule.id in seen_ids:
                duplicate_ids.add(rule.id)
            else:
                seen_ids.add(rule.id)
        if duplicate_ids:
            raise HTTPException(
                status_code=500,
                detail=f"Stored global routing rule ID '{min(duplicate_ids)}' is duplicated.",
            )
        return validated_rules

    @staticmethod
    def _build_rule_response(
        payload: Any,
        *,
        invalid_detail: str = "Stored global routing rule is invalid.",
    ) -> GlobalRoutingRuleResponse:
        if not isinstance(payload, Mapping):
            raise HTTPException(status_code=500, detail=invalid_detail)

        try:
            return GlobalRoutingRuleResponse.model_validate(dict(payload))
        except ValidationError as exc:
            raise HTTPException(status_code=500, detail=invalid_detail) from exc
