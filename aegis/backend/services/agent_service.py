from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from aegis.backend.models import AgentResponse, AgentUpsertRequest, OverviewAgentResponse
from aegis.backend.services.store import AegisStore, get_aegis_store


class AgentService:
    _LEGACY_AGENT_DEFAULTS = {
        "description": "",
        "headers": {},
        "status": "offline",
        "extcapabilities": [],
    }

    def __init__(self, store: AegisStore | None = None) -> None:
        # The server must still expose health and authentication endpoints when
        # the optional A2A registry is malformed.  Resolve the registry only
        # when an Agent operation is actually requested.
        self._store = store

    def _get_store(self) -> AegisStore:
        if self._store is None:
            self._store = get_aegis_store()
        return self._store

    def list_agents(self) -> list[AgentResponse]:
        payload = self._get_store().read_locked()
        agents = payload["a2a"]
        return [
            self._build_agent_response(agent_id, value)
            for agent_id, value in sorted(agents.items())
        ]

    def list_overview_agents(self) -> list[OverviewAgentResponse]:
        return [
            self._build_overview_agent_response(agent)
            for agent in self.list_agents()
        ]

    def get_agent(self, agent_id: str) -> AgentResponse:
        payload = self._get_store().read_locked()
        agent = payload["a2a"].get(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")
        return self._build_agent_response(agent_id, agent)

    def create_agent(self, agent_id: str, body: AgentUpsertRequest) -> AgentResponse:
        def _mutate(payload: dict[str, Any]) -> AgentResponse:
            agents = payload["a2a"]
            if agent_id in agents:
                raise HTTPException(
                    status_code=409,
                    detail=f"Agent '{agent_id}' already exists.",
                )
            agents[agent_id] = body.model_dump(mode="json")
            return self._build_agent_response(agent_id, agents[agent_id])

        return self._get_store().mutate_locked(_mutate)

    def update_agent(self, agent_id: str, body: AgentUpsertRequest) -> AgentResponse:
        def _mutate(payload: dict[str, Any]) -> AgentResponse:
            agents = payload["a2a"]
            if agent_id not in agents:
                raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")
            agents[agent_id] = body.model_dump(mode="json")
            return self._build_agent_response(agent_id, agents[agent_id])

        return self._get_store().mutate_locked(_mutate)

    def delete_agent(self, agent_id: str) -> None:
        def _mutate(payload: dict[str, Any]) -> None:
            agents = payload["a2a"]
            if agent_id not in agents:
                raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")
            del agents[agent_id]

        self._get_store().mutate_locked(_mutate)

    @staticmethod
    def _build_agent_response(agent_id: str, payload: Mapping[str, Any] | str) -> AgentResponse:
        normalized_payload = AgentService._normalize_stored_agent_payload(agent_id, payload)

        try:
            return AgentResponse.model_validate({"agent_id": agent_id, **dict(normalized_payload)})
        except ValidationError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Stored agent '{agent_id}' has an invalid shape.",
            ) from exc

    @classmethod
    def _normalize_stored_agent_payload(
        cls, agent_id: str, payload: Mapping[str, Any] | str
    ) -> Mapping[str, Any]:
        if isinstance(payload, str):
            return {"url": payload, **deepcopy(cls._LEGACY_AGENT_DEFAULTS)}
        if isinstance(payload, Mapping):
            return payload
        raise HTTPException(
            status_code=500,
            detail=f"Stored agent '{agent_id}' has an invalid shape.",
        )

    @staticmethod
    def _build_overview_agent_response(agent: AgentResponse) -> OverviewAgentResponse:
        return OverviewAgentResponse.model_validate(
            {
                "agent_id": agent.agent_id,
                "url": agent.url,
                "description": agent.description,
                "status": agent.status,
                "extcapabilities": agent.extcapabilities,
            }
        )
