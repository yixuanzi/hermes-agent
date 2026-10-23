"""Mid-turn steering of a running Workagent A2A task."""

from __future__ import annotations

import asyncio
import threading
import time

import httpx
import pytest

from run_agent import AIAgent
from tools.a2a_delegate_tool import (
    _A2ADelegateSession,
    _summarize_agent_card,
    _a2a_steer_path,
)
from workagent.backend.a2a_server import create_a2a_app
from workagent.backend.config import WorkagentSettings


class _ToolBatchAgent:
    """Stands in for a remote AIAgent: steer is drained at each tool boundary."""

    steer = AIAgent.steer
    _drain_pending_steer = AIAgent._drain_pending_steer
    instances: list["_ToolBatchAgent"] = []

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._pending_steer = None
        self._pending_steer_lock = threading.Lock()
        self.inputs: list[str] = []
        self.started = threading.Event()
        type(self).instances.append(self)

    def run_conversation(self, user_message, *args, **kwargs):
        del args, kwargs
        self.inputs.append(user_message)
        self.started.set()
        seen = []
        for _batch in range(5):
            time.sleep(0.1)
            steer = self._drain_pending_steer()
            if steer:
                seen.append(steer)
        return {"final_response": f"steers={seen}"}


class _LateSteerAgent:
    """The first run ends with a steer that missed every tool batch."""

    instances: list["_LateSteerAgent"] = []

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.inputs: list[str] = []
        type(self).instances.append(self)

    def run_conversation(self, user_message, *args, **kwargs):
        del args, kwargs
        self.inputs.append(user_message)
        if len(self.inputs) == 1:
            return {"final_response": "first answer", "pending_steer": "also check Y"}
        return {"final_response": f"applied: {user_message}"}


class _RacingSteerAgent:
    """A steer lands after the agent's own final drain, as the turn ends."""

    steer = AIAgent.steer
    _drain_pending_steer = AIAgent._drain_pending_steer
    instances: list["_RacingSteerAgent"] = []

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._pending_steer = None
        self._pending_steer_lock = threading.Lock()
        self.inputs: list[str] = []
        type(self).instances.append(self)

    def run_conversation(self, user_message, *args, **kwargs):
        del args, kwargs
        self.inputs.append(user_message)
        if len(self.inputs) == 1:
            self.steer("late arrival")
        return {"final_response": f"answered: {user_message}"}


class _EndlessSteerAgent:
    instances: list["_EndlessSteerAgent"] = []

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.inputs: list[str] = []
        type(self).instances.append(self)

    def run_conversation(self, user_message, *args, **kwargs):
        del args, kwargs
        self.inputs.append(user_message)
        return {"final_response": "again", "pending_steer": f"steer {len(self.inputs)}"}


def _session(transport, card: dict | None = None) -> _A2ADelegateSession:
    entry = {"agent_card": card} if card is not None else {}
    session = _A2ADelegateSession(
        "http://testserver",
        poll_interval=0.02,
        timeout=10,
        steer_path=_a2a_steer_path(entry),
    )
    session._build_http_client = lambda: httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    )
    return session


async def _wait_for(predicate, *, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_agent_card_declares_steer_path():
    app = create_a2a_app(WorkagentSettings(host="testserver", port=9120), agent_factory=_ToolBatchAgent)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        card = (await client.get("/.well-known/agent-card.json")).json()

    assert _a2a_steer_path({"agent_card": _summarize_agent_card(card)}) == "/a2a/hermes/steer"


@pytest.mark.asyncio
async def test_steer_reaches_the_running_turn_without_a_new_turn():
    _ToolBatchAgent.instances.clear()
    app = create_a2a_app(WorkagentSettings(host="testserver", port=9120), agent_factory=_ToolBatchAgent)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        card = _summarize_agent_card((await client.get("/.well-known/agent-card.json")).json())
    session = _session(transport, card)

    turn = asyncio.create_task(session.send_turn("long task", is_delegate_output=False))
    await _wait_for(lambda: _ToolBatchAgent.instances and _ToolBatchAgent.instances[0].started.is_set())
    await _wait_for(lambda: session._turn_task_id is not None)
    future = session.schedule_steer("focus on X only")
    assert await asyncio.wrap_future(future) is True
    result = await asyncio.wait_for(turn, timeout=5)
    await session.close()

    agent = _ToolBatchAgent.instances[0]
    assert agent.inputs == ["long task"]
    assert "focus on X only" in result["final_response"]
    assert result["state_name"] == "completed"
    assert session.schedule_steer("too late") is None


@pytest.mark.asyncio
async def test_leftover_steer_continues_the_same_task():
    _LateSteerAgent.instances.clear()
    app = create_a2a_app(WorkagentSettings(host="testserver", port=9120), agent_factory=_LateSteerAgent)
    session = _session(httpx.ASGITransport(app=app))

    result = await asyncio.wait_for(session.send_turn("start", is_delegate_output=False), timeout=5)
    await session.close()

    assert _LateSteerAgent.instances[0].inputs == ["start", "also check Y"]
    assert result["final_response"] == "applied: also check Y"
    assert result["state_name"] == "completed"


@pytest.mark.asyncio
async def test_steer_racing_the_end_of_a_turn_is_not_left_on_the_agent():
    _RacingSteerAgent.instances.clear()
    app = create_a2a_app(WorkagentSettings(host="testserver", port=9120), agent_factory=_RacingSteerAgent)
    session = _session(httpx.ASGITransport(app=app))

    result = await asyncio.wait_for(session.send_turn("start", is_delegate_output=False), timeout=5)
    await session.close()

    agent = _RacingSteerAgent.instances[0]
    assert agent.inputs == ["start", "late arrival"]
    assert agent._pending_steer is None
    assert result["final_response"] == "answered: late arrival"


@pytest.mark.asyncio
async def test_leftover_steer_continuations_are_bounded():
    _EndlessSteerAgent.instances.clear()
    app = create_a2a_app(WorkagentSettings(host="testserver", port=9120), agent_factory=_EndlessSteerAgent)
    session = _session(httpx.ASGITransport(app=app))

    result = await asyncio.wait_for(session.send_turn("start", is_delegate_output=False), timeout=5)
    await session.close()

    assert len(_EndlessSteerAgent.instances[0].inputs) == 4
    assert result["state_name"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "status_code"),
    [
        ({"task_id": "missing", "context_id": "ctx", "text": "hi"}, 409),
        ({"task_id": "", "context_id": "ctx", "text": "hi"}, 422),
        ({"task_id": "t", "context_id": "ctx", "text": "  "}, 422),
    ],
)
async def test_steer_rejects_unknown_tasks_and_empty_fields(payload, status_code):
    app = create_a2a_app(WorkagentSettings(host="testserver", port=9120), agent_factory=_ToolBatchAgent)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post("/a2a/hermes/steer", json=payload)
    assert response.status_code == status_code


@pytest.mark.asyncio
async def test_steer_rejects_a_mismatched_context():
    _ToolBatchAgent.instances.clear()
    app = create_a2a_app(WorkagentSettings(host="testserver", port=9120), agent_factory=_ToolBatchAgent)
    transport = httpx.ASGITransport(app=app)
    session = _session(transport)
    turn = asyncio.create_task(session.send_turn("long task", is_delegate_output=False))
    await _wait_for(lambda: session._turn_task_id is not None)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/a2a/hermes/steer",
            json={"task_id": session._turn_task_id, "context_id": "other-context", "text": "hi"},
        )
    await asyncio.wait_for(turn, timeout=5)
    await session.close()

    assert response.status_code == 409
    assert _ToolBatchAgent.instances[0].inputs == ["long task"]


@pytest.mark.asyncio
async def test_steer_requires_a2a_authentication():
    settings = WorkagentSettings(
        host="testserver",
        port=9120,
        a2a_auth_enabled=True,
        a2a_session_token="a2a-secret",
        a2a_token_source="env",
    )
    app = create_a2a_app(settings, agent_factory=_ToolBatchAgent)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/a2a/hermes/steer", json={"task_id": "t", "context_id": "c", "text": "hi"}
        )
    assert response.status_code == 401
