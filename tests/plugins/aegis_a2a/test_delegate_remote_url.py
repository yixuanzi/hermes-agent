"""aegis_a2a_delegate connects to the a2a.json address, not the address a remote's card advertises."""

import httpx
import pytest
from a2a.helpers.proto_helpers import new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import add_a2a_routes_to_fastapi, create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, Part
from a2a.utils.constants import PROTOCOL_VERSION_CURRENT, TransportProtocol
from fastapi import FastAPI

from plugins.aegis_a2a.delegate_tool import _A2ADelegateSession, _resolve_a2a_remote_url, _run_coro_sync

CONFIGURED = "http://172.18.40.74:9086/a2a"
BIND_ADDRESS = "http://0.0.0.0:9086/a2a"


class _Echo(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task or new_task_from_user_message(context.message)
        if context.current_task is None:
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        text = "".join(p.text for p in context.message.parts if p.text).rsplit("\n\n", 1)[-1]
        await updater.complete(updater.new_agent_message([Part(text=f"echo: {text}")]))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError


def _remote_advertising(url: str) -> FastAPI:
    card = AgentCard(
        name="aisoc", description="remote", version="1",
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["text/plain"], default_output_modes=["text/plain"],
        supported_interfaces=[AgentInterface(url=url, protocol_binding=TransportProtocol.JSONRPC,
                                             protocol_version=PROTOCOL_VERSION_CURRENT)],
    )
    handler = DefaultRequestHandler(agent_executor=_Echo(), task_store=InMemoryTaskStore(), agent_card=card)
    app = FastAPI()
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card, card_url="/a2a/.well-known/agent-card.json"),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/a2a"),
    )
    return app


class _Network(httpx.AsyncBaseTransport):
    """Only the configured host answers; any other host is a different machine (the 401 test agent)."""

    def __init__(self, app, host):
        self._app, self._host, self.hosts = httpx.ASGITransport(app=app), host, []

    async def handle_async_request(self, request):
        self.hosts.append(request.url.netloc.decode())
        if request.url.netloc.decode() != self._host:
            return httpx.Response(401, request=request)
        return await self._app.handle_async_request(request)


def test_delegation_reaches_the_configured_address_when_the_card_advertises_its_bind_address():
    entry = {"url": CONFIGURED, "agent_card": {"supported_interfaces": [{"url": BIND_ADDRESS}]}}
    url = _resolve_a2a_remote_url(entry)
    network = _Network(_remote_advertising(BIND_ADDRESS), "172.18.40.74:9086")
    session = _A2ADelegateSession(url, poll_interval=0)
    session._build_http_client = lambda: httpx.AsyncClient(transport=network)

    async def run():
        try:
            return await session.send_turn("hello aisoc")
        finally:
            await session.close()

    result = _run_coro_sync(run())

    assert url == CONFIGURED
    assert result["final_response"] == "echo: hello aisoc"
    assert set(network.hosts) == {"172.18.40.74:9086"}


@pytest.mark.parametrize(
    ("configured", "advertised", "expected"),
    [
        # same origin: the card may name an RPC path other than its base
        ("http://agent.local/a2a", "http://agent.local/a2a/jsonrpc", "http://agent.local/a2a/jsonrpc"),
        # behind a gateway prefix: the advertised internal URL is unreachable from here
        ("https://gw.example/a2a/agents/x", "http://10.0.0.5:9086/a2a", "https://gw.example/a2a/agents/x"),
        ("http://agent.local/a2a/", "", "http://agent.local/a2a"),
    ],
)
def test_card_url_is_used_only_on_the_configured_origin(configured, advertised, expected):
    card = {"supported_interfaces": [{"url": advertised}]} if advertised else {}
    assert _resolve_a2a_remote_url({"url": configured, "agent_card": card}) == expected
