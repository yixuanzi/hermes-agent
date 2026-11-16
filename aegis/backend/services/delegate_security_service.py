"""Aegis API service for A2A Agent Policy and delegate audit records."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from aegis.backend.models import (
    AgentPolicyResponse,
    AgentPolicyUpsertRequest,
    DelegateAuditListResponse,
    DelegateAuditResponse,
    OverviewStatsResponse,
    TaskAuditListResponse,
    TaskAuditResponse,
)
from aegis.backend.chat.service import TaskAuditStore, get_task_audit_store
from tools.a2a_delegate_aegis import (
    AegisDelegateStore,
    AgentPolicyConflictError,
    AgentPolicyNotFoundError,
    get_aegis_delegate_store,
)


class DelegateSecurityService:
    def __init__(
        self,
        store: AegisDelegateStore | None = None,
        task_audit_store: TaskAuditStore | None = None,
    ) -> None:
        self._store = store or get_aegis_delegate_store()
        self._task_audit_store = task_audit_store or get_task_audit_store()

    def list_policies(self) -> list[AgentPolicyResponse]:
        return [AgentPolicyResponse.model_validate(row) for row in self._store.list_policies()]

    def get_policy(self, rank_id: int) -> AgentPolicyResponse:
        try:
            row = self._store.get_policy(rank_id)
        except AgentPolicyNotFoundError as exc:
            raise self._not_found(rank_id) from exc
        return AgentPolicyResponse.model_validate(row)

    def create_policy(self, body: AgentPolicyUpsertRequest) -> AgentPolicyResponse:
        try:
            row = self._store.create_policy(**body.model_dump())
        except AgentPolicyConflictError as exc:
            raise self._conflict(body.rank_id) from exc
        return AgentPolicyResponse.model_validate(row)

    def update_policy(
        self,
        current_rank_id: int,
        body: AgentPolicyUpsertRequest,
    ) -> AgentPolicyResponse:
        try:
            row = self._store.update_policy(current_rank_id, **body.model_dump())
        except AgentPolicyNotFoundError as exc:
            raise self._not_found(current_rank_id) from exc
        except AgentPolicyConflictError as exc:
            raise self._conflict(body.rank_id) from exc
        return AgentPolicyResponse.model_validate(row)

    def delete_policy(self, rank_id: int) -> None:
        try:
            self._store.delete_policy(rank_id)
        except AgentPolicyNotFoundError as exc:
            raise self._not_found(rank_id) from exc

    def query_audits(self, **filters: Any) -> DelegateAuditListResponse:
        page = self._store.query_audits(**filters)
        return DelegateAuditListResponse(
            logs=[DelegateAuditResponse.model_validate(row) for row in page.logs],
            total=page.total,
            page=page.page,
            page_size=page.page_size,
        )

    def query_task_audits(self, **filters: Any) -> TaskAuditListResponse:
        page = self._task_audit_store.query_task_audits(**filters)
        return TaskAuditListResponse(
            logs=[TaskAuditResponse.model_validate(row) for row in page.logs],
            total=page.total,
            page=page.page,
            page_size=page.page_size,
        )

    def get_overview_stats(self) -> OverviewStatsResponse:
        return OverviewStatsResponse.model_validate(self._store.get_overview_stats())

    @staticmethod
    def _not_found(rank_id: int) -> HTTPException:
        return HTTPException(
            status_code=404,
            detail=f"Agent Policy rank '{rank_id}' not found.",
        )

    @staticmethod
    def _conflict(rank_id: int) -> HTTPException:
        return HTTPException(
            status_code=409,
            detail=f"Agent Policy rank '{rank_id}' already exists.",
        )
