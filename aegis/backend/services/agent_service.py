from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import logging
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from aegis.backend.models import (
    AgentResponse,
    AgentUpsertRequest,
    InvalidAgentResponse,
    OverviewAgentResponse,
)
from aegis.backend.services.store import AegisStore, get_aegis_store


logger = logging.getLogger(__name__)


class InvalidStoredAgentError(ValueError):
    """An ``a2a.json`` entry that does not form a valid agent."""


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
        return self.scan_agents()[0]

    def scan_agents(self) -> tuple[list[AgentResponse], list[InvalidAgentResponse]]:
        """Every stored agent that parses, and the entries that do not.

        One hand-edited bad entry must not 500 the agent list, the overview and every chat
        message (shortcut resolution lists agents); it is skipped with a warning and stays
        editable and deletable by id.
        """
        valid: list[AgentResponse] = []
        invalid: list[InvalidAgentResponse] = []
        for agent_id, value in sorted(self._get_store().read_locked()["a2a"].items()):
            try:
                valid.append(self._parse_stored_agent(agent_id, value))
            except InvalidStoredAgentError as exc:
                logger.warning("Skipping invalid a2a.json agent %r: %s", agent_id, exc)
                invalid.append(InvalidAgentResponse(agent_id=agent_id, error=str(exc)))
        return valid, invalid

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

    @classmethod
    def _build_agent_response(cls, agent_id: str, payload: Mapping[str, Any] | str) -> AgentResponse:
        try:
            return cls._parse_stored_agent(agent_id, payload)
        except InvalidStoredAgentError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Stored agent '{agent_id}' has an invalid shape.",
            ) from exc

    @classmethod
    def _parse_stored_agent(cls, agent_id: str, payload: Mapping[str, Any] | str) -> AgentResponse:
        if isinstance(payload, str):
            payload = {"url": payload, **deepcopy(cls._LEGACY_AGENT_DEFAULTS)}
        if not isinstance(payload, Mapping):
            raise InvalidStoredAgentError("entry must be a URL string or an object")
        try:
            return AgentResponse.model_validate({"agent_id": agent_id, **dict(payload)})
        except ValidationError as exc:
            raise InvalidStoredAgentError(
                "; ".join(
                    f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                    for error in exc.errors()
                )
            ) from exc

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
