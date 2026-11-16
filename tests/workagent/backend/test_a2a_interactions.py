from __future__ import annotations

import asyncio

import httpx
import pytest
from a2a.client import ClientConfig, ClientFactory
from a2a.types import (
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
)

from tools.approval import (
    _await_gateway_decision,
    _gateway_notify_cbs,
    get_current_session_key,
)
from workagent.backend.a2a_server import create_a2a_app
from workagent.backend.config import WorkagentSettings
from tools.a2a_delegate_tool import _A2ADelegateSession


class _ApprovalAgent:
    def __init__(self, session_id: str):
        self.session_id = session_id

    def run_conversation(self, *args, **kwargs):
        del args, kwargs
        session_key = get_current_session_key()
        decision = _await_gateway_decision(
            session_key,
            _gateway_notify_cbs[session_key],
            {
                "command": "chmod 777 ./artifact",
                "description": "world-writable permissions",
                "pattern_key": "world-writable permissions",
                "pattern_keys": ["world-writable permissions"],
                "allow_session": True,
                "allow_permanent": True,
            },
        )
        return {"final_response": f"approval:{decision['choice']}"}


class _ClarifyAgent:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.answer = None

    def run_conversation(self, *args, **kwargs):
        del args, kwargs
        self.answer = self.clarify_callback(
            "Which targets should be checked?",
            ["linux", "windows", "macos"],
            multi_select=True,
        )
        return {"final_response": f"clarify:{self.answer}"}


async def _start_request(client, text: str):
    request = SendMessageRequest(
        message=Message(
            message_id="user-message",
            role=Role.ROLE_USER,
            context_id="",
            task_id="",
            parts=[Part(text=text)],
        ),
        configuration=SendMessageConfiguration(return_immediately=True),
    )
    events = []

    async def consume():
        async for event in client.send_message(request):
            events.append(event)

    task = asyncio.create_task(consume())
    return task, events


@pytest.mark.asyncio
async def test_workagent_a2a_approval_round_trip_is_authenticated_and_single_use():
    settings = WorkagentSettings(
        host="testserver",
        port=9120,
        a2a_auth_enabled=True,
        a2a_session_token="a2a-secret",
        a2a_token_source="env",
    )
    app = create_a2a_app(settings, agent_factory=_ApprovalAgent)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Authorization": "Bearer a2a-secret"},
    ) as http_client:
        card = await http_client.get("/.well-known/agent-card.json")
        assert card.status_code == 200
        extension = next(item for item in card.json()["extensions"] if item["uri"].endswith("interaction/v1"))
        assert extension["params"]["response_path"] == "/a2a/hermes/interaction/respond"

        factory = ClientFactory(
            ClientConfig(httpx_client=http_client, streaming=False, polling=True)
        )
        client = await factory.create_from_url("http://testserver")
        send_task, _events = await _start_request(client, "run the guarded operation")
        for _ in range(100):
            if app.state.a2a_executor.interactions._records:
                break
            await asyncio.sleep(0.01)
        record = next(iter(app.state.a2a_executor.interactions._records.values()))

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as unauthenticated:
            unauthorized = await unauthenticated.post(
                "/a2a/hermes/interaction/respond",
                json={
                    "task_id": record.task_id,
                    "context_id": record.context_id,
                    "interaction_id": record.interaction_id,
                    "kind": "approval",
                    "choice": "once",
                },
            )
        assert unauthorized.status_code == 401

        response = await http_client.post(
            "/a2a/hermes/interaction/respond",
            json={
                "task_id": record.task_id,
                "context_id": record.context_id,
                "interaction_id": record.interaction_id,
                "kind": "approval",
                "choice": "once",
            },
        )
        assert response.status_code == 200
        assert record.state == "resolved"
        await asyncio.wait_for(send_task, timeout=3)

        duplicate = await http_client.post(
            "/a2a/hermes/interaction/respond",
            json={
                "task_id": record.task_id,
                "context_id": record.context_id,
                "interaction_id": record.interaction_id,
                "kind": "approval",
                "choice": "deny",
            },
        )
        assert duplicate.status_code == 409
        await client.close()


@pytest.mark.asyncio
async def test_workagent_a2a_clarify_accepts_multi_select_json_answer():
    settings = WorkagentSettings(host="testserver", port=9120)
    app = create_a2a_app(settings, agent_factory=_ClarifyAgent)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        factory = ClientFactory(
            ClientConfig(httpx_client=http_client, streaming=False, polling=True)
        )
        client = await factory.create_from_url("http://testserver")
        send_task, _events = await _start_request(client, "ask for target selection")
        for _ in range(100):
            if app.state.a2a_executor.interactions._records:
                break
            await asyncio.sleep(0.01)
        record = next(iter(app.state.a2a_executor.interactions._records.values()))
        assert record.kind == "clarify"
        assert record.payload["multi_select"] is True

        mismatch = await http_client.post(
            "/a2a/hermes/interaction/respond",
            json={
                "task_id": "wrong",
                "context_id": record.context_id,
                "interaction_id": record.interaction_id,
                "kind": "clarify",
                "answer": ["linux", "macos"],
            },
        )
        assert mismatch.status_code == 409

        response = await http_client.post(
            "/a2a/hermes/interaction/respond",
            json={
                "task_id": record.task_id,
                "context_id": record.context_id,
                "interaction_id": record.interaction_id,
                "kind": "clarify",
                "answer": ["linux", "macos"],
            },
        )
        assert response.status_code == 200
        await asyncio.wait_for(send_task, timeout=3)
        await client.close()


def test_a2a_delegate_client_deduplicates_interaction_metadata():
    class _Output:
        def __init__(self):
            self.events = []

        def emit(self, source, event_type, content, *, session_id=None):
            self.events.append((source, event_type, content, session_id))

    output = _Output()
    session = _A2ADelegateSession(
        "http://remote/a2a",
        output=output,
        session_id="ctx-1",
        interaction_supported=True,
    )
    session.task_id = "task-1"
    message = Message(
        message_id="interaction-message",
        role=Role.ROLE_AGENT,
        context_id="ctx-1",
        task_id="task-1",
        parts=[Part(text="")],
        metadata={
            "hermes": {
                "kind": "approval_request",
                "interaction_id": "approval-1",
                "task_id": "task-1",
                "context_id": "ctx-1",
                "command": "chmod 777 x",
                "choices": ["once", "deny"],
            }
        },
    )
    session._emit_tool_messages([message], session_id="ctx-1")
    session._emit_tool_messages([message], session_id="ctx-1")

    assert len(output.events) == 1
    assert output.events[0][1] == "approval_request"
    assert '"interaction_id":"approval-1"' in output.events[0][2]
