"""Overview routes for Aegis backend."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from aegis.backend.auth import require_authenticated_user
from aegis.backend.config import AegisSettings
from aegis.backend.models import (
    OverviewAgentListResponse,
    OverviewStatsResponse,
    TopologyStarmappingResponse,
)
from aegis.backend.services.agent_service import AgentService
from aegis.backend.services.delegate_security_service import DelegateSecurityService
from aegis.backend.services.user_service import UserService


_STARMAPPING_PATH = Path(__file__).resolve().parents[2] / "aegis_starmapping.json"


def _load_starmapping() -> dict[str, Any]:
    """Load the editable topology baseline on every request.

    Keeping the design contract on disk lets topology edits reach the console
    without copying a second static graph into Python or TypeScript.
    """
    try:
        return json.loads(_STARMAPPING_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to load Aegis starmapping: {_STARMAPPING_PATH}") from exc


def build_overview_router(
    settings: AegisSettings,
    user_service: UserService,
    agent_service: AgentService | None = None,
    delegate_security_service: DelegateSecurityService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/overview", tags=["overview"])

    def _service() -> AgentService:
        return agent_service or AgentService()

    def _delegate_security_service() -> DelegateSecurityService:
        return delegate_security_service or DelegateSecurityService()

    def _ensure_authenticated(request: Request) -> None:
        require_authenticated_user(request, settings, user_service)

    def _topology_response() -> TopologyStarmappingResponse:
        baseline = _load_starmapping()
        runtime_status = {
            agent.agent_id: agent.status
            for agent in _service().list_overview_agents()
        }
        agents = []
        edges = list(baseline["relationships"]["center_to_agents"])

        for agent in baseline["agent_ring"]:
            agent_payload = dict(agent)
            agent_payload["product_service_code"] = agent.get("product_service_code") or (
                f"WORKAGENT-{agent['business_domain']}"
            )
            agent_payload["runtime"] = {
                "status": runtime_status.get(agent["id"], "planned"),
                "source": "a2a_registry" if agent["id"] in runtime_status else "starmapping_baseline",
            }
            agents.append(agent_payload)
            edges.extend(
                {
                    "source": agent["id"],
                    "target": star["id"],
                    "mode": "requires",
                }
                for star in agent["star_nodes"]
            )

        return TopologyStarmappingResponse.model_validate(
            {
                "schema_version": baseline["schema_version"],
                "updated_at": baseline["updated_at"],
                "center": baseline["center"],
                "agents": agents,
                "edges": edges,
            }
        )

    @router.get("/agents", response_model=OverviewAgentListResponse)
    async def list_overview_agents(request: Request) -> OverviewAgentListResponse:
        _ensure_authenticated(request)
        return OverviewAgentListResponse(agents=_service().list_overview_agents())

    @router.get("/stats", response_model=OverviewStatsResponse)
    async def get_overview_stats(request: Request) -> OverviewStatsResponse:
        _ensure_authenticated(request)
        return _delegate_security_service().get_overview_stats()

    @router.get("/topology", response_model=TopologyStarmappingResponse)
    async def get_overview_topology(request: Request) -> TopologyStarmappingResponse:
        """Return the current three-layer graph plus registry-backed agent state."""
        _ensure_authenticated(request)
        return _topology_response()

    return router
