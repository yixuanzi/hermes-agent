"""WORKAGENT A2A server entrypoint backed by the official A2A SDK."""

from __future__ import annotations

import json
import os
from pathlib import Path

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.request_handlers.response_helpers import agent_card_to_dict
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_jsonrpc_routes,
)
from a2a.types import AgentCapabilities, AgentCard, AgentInterface
from a2a.utils.constants import PROTOCOL_VERSION_CURRENT, TransportProtocol
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn

from workagent.backend.a2a_management import is_management_path, register_management_routes
from workagent.backend.a2a_service import BoundedTaskStore, HermesA2AExecutor
from workagent.backend.a2a_service.mcp_keys import KEY_PREFIX as MCP_KEY_PREFIX, McpKeyStore
from workagent.backend.a2a_service.mcp_tasks import (
    MCP_KEY_STATE_ATTR,
    MCP_PATH,
    create_task_mcp_server,
    mount_task_mcp,
)
from workagent.backend.agent_runtime import prepare_hermes_home, start_workagent_mcp_bootstrap
from workagent.backend.auth import extract_bearer_token, verify_bearer_token_value
from workagent.backend.config import WorkagentSettings, is_loopback_host, load_workagent_settings
from workagent.backend.a2a_service.interactions import (
    INTERACTION_EXTENSION_URI,
    INTERACTION_RESPONSE_SUFFIX,
    STEER_REQUEST_SUFFIX,
    InteractionError,
    interaction_extension,
)

A2A_RPC_PATH = os.getenv("A2A_BASE_PATH", "/a2a")
A2A_AGENT_CARD_PATH = f"{A2A_RPC_PATH}/.well-known/agent-card.json"
A2A_INTERACTION_RESPONSE_PATH = f"{A2A_RPC_PATH}{INTERACTION_RESPONSE_SUFFIX}"
A2A_STEER_PATH = f"{A2A_RPC_PATH}{STEER_REQUEST_SUFFIX}"
A2A_PUBLIC_PATHS = frozenset(
    {
        "/health",
        "/.well-known/agent-card.json",
        A2A_AGENT_CARD_PATH,
    }
)
print(f"A2A RPC path: {A2A_RPC_PATH}")
print(f"MCP (streamable HTTP) path: {MCP_PATH}")

def build_agent_card(
    settings: WorkagentSettings,
    *,
    name: str | None = None,
    description: str | None = None,
    card_path: str | None = None,
    streaming: bool = False,
) -> AgentCard:
    """Build an A2A AgentCard for this server."""
    if card_path:
        data = json.loads(Path(card_path).read_text(encoding="utf-8"))
        # A2A SDK 1.x AgentCard is a protobuf and does not yet expose the
        # optional Hermes extensions field.  The server adds the extension to
        # the public JSON route below, so keep custom card files compatible by
        # dropping only this transport-level field before protobuf parsing.
        data.pop("extensions", None)
        data.pop("supportedExtensions", None)
        return AgentCard(**data)

    rpc_url = f"http://{settings.host}:{settings.port}{A2A_RPC_PATH}"
    return AgentCard(
        name=name or "Hermes Agent",
        description=description or "Hermes WORKAGENT A2A module.",
        version="0.1.0",
        capabilities=AgentCapabilities(streaming=streaming, push_notifications=False),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        supported_interfaces=[
            AgentInterface(
                url=rpc_url,
                protocol_binding=TransportProtocol.JSONRPC,
                protocol_version=PROTOCOL_VERSION_CURRENT,
            )
        ],
        skills=[],
    )


def create_a2a_app(
    settings: WorkagentSettings | None = None,
    *,
    agent_factory=None,
    name: str | None = None,
    description: str | None = None,
    card_path: str | None = None,
    streaming: bool = False,
    workers: int = 4,
    mcp_key_store: McpKeyStore | None = None,
) -> FastAPI:
    """Create the WORKAGENT A2A FastAPI application."""
    del workers
    active_settings = settings or load_workagent_settings(open_browser=False)
    key_store = mcp_key_store if mcp_key_store is not None else McpKeyStore()
    app = FastAPI(title="WORKAGENT A2A")
    app.state.workagent_settings = active_settings
    app.state.mcp_key_store = key_store

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        path = request.url.path
        if is_management_path(path):
            origin = (request.headers.get("Origin") or "").strip()
            service_origin = f"{request.url.scheme}://{request.url.netloc}"
            if origin and origin != service_origin:
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "Cross-origin management requests are forbidden"},
                )
        if path == MCP_PATH:
            # A personal MCP key is accepted here and only here; the MCP tools
            # read it back from request.state to apply its identity.
            presented = extract_bearer_token(request)
            key = key_store.authenticate(presented)
            if key is not None:
                setattr(request.state, MCP_KEY_STATE_ATTR, key)
                return await call_next(request)
            if presented and presented.startswith(MCP_KEY_PREFIX):
                # A deleted or disabled personal key is refused even when A2A
                # auth is off, rather than quietly running as an anonymous caller.
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
        if (
            active_settings.a2a_auth_enabled
            and path not in A2A_PUBLIC_PATHS
            and not is_management_path(path)
        ):
            try:
                verify_bearer_token_value(request, active_settings.a2a_session_token)
            except HTTPException as exc:
                return JSONResponse(status_code=401, content={"detail": exc.detail})
        return await call_next(request)

    # Added after (so outside) the auth middleware: a browser preflight carries
    # no Authorization header and must be answered before auth, and a 401 needs
    # CORS headers for the caller to see it. Non-preflight requests, plain
    # OPTIONS included, still pass through auth.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict[str, str | int]:
        return {"status": "ok", "module": "a2a", "pid": os.getpid()}

    register_management_routes(app, active_settings, key_store)

    agent_card = build_agent_card(
        active_settings,
        name=name,
        description=description,
        card_path=card_path,
        streaming=streaming,
    )
    # Bounded rather than the SDK's InMemoryTaskStore: nothing in the A2A
    # flow deletes a finished task, so an unbounded store keeps one record
    # per request for the life of the process.
    task_store = BoundedTaskStore()
    a2a_executor = HermesA2AExecutor(
        agent_factory=agent_factory,
        enable_streaming=streaming,
    )
    app.state.a2a_executor = a2a_executor
    request_handler = DefaultRequestHandler(
        agent_executor=a2a_executor,
        task_store=task_store,
        agent_card=agent_card,
    )

    def _agent_card_payload() -> dict:
        payload = agent_card_to_dict(agent_card)
        extensions = payload.setdefault("extensions", [])
        if not any(
            isinstance(item, dict)
            and item.get("uri") == INTERACTION_EXTENSION_URI
            for item in extensions
        ):
            extensions.append(
                interaction_extension(A2A_INTERACTION_RESPONSE_PATH, A2A_STEER_PATH)
            )
        return payload

    @app.get("/.well-known/agent-card.json", include_in_schema=False)
    async def root_agent_card() -> JSONResponse:
        return JSONResponse(_agent_card_payload())

    @app.get(A2A_AGENT_CARD_PATH, include_in_schema=False)
    async def a2a_agent_card() -> JSONResponse:
        return JSONResponse(_agent_card_payload())

    @app.post(A2A_INTERACTION_RESPONSE_PATH, include_in_schema=False)
    async def respond_to_interaction(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=422, detail="request body must be JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="request body must be an object")
        try:
            record = a2a_executor.interactions.resolve(payload)
        except InteractionError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        try:
            await a2a_executor.publish_interaction_resolved(record)
        except Exception:
            # The underlying wait has already been released.  Do not turn a
            # successful user response into a retry that could look like a
            # duplicate click; log-only is the safe failure mode here.
            import logging

            logging.getLogger(__name__).warning(
                "Failed to publish resolved A2A interaction %s",
                record.interaction_id,
                exc_info=True,
            )
        return JSONResponse(
            {
                "ok": True,
                "interaction_id": record.interaction_id,
                "kind": record.kind,
                "state": record.state,
            }
        )

    @app.post(A2A_STEER_PATH, include_in_schema=False)
    async def steer_running_task(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=422, detail="request body must be JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="request body must be an object")
        try:
            result = await a2a_executor.steer(payload)
        except InteractionError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return JSONResponse(result)

    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=[],
        jsonrpc_routes=create_jsonrpc_routes(request_handler, rpc_url=A2A_RPC_PATH),
    )

    # /mcp is not in A2A_PUBLIC_PATHS, so auth_middleware guards it with the
    # same A2A session token.
    task_mcp = create_task_mcp_server(
        request_handler, a2a_executor, name=agent_card.name or "Hermes Agent"
    )
    app.router.routes.extend(mount_task_mcp(task_mcp, host=active_settings.host))
    app.router.lifespan_context = lambda _app: task_mcp.session_manager.run()
    app.state.task_mcp = task_mcp
    return app


def start_a2a_server(
    *,
    host: str = "127.0.0.1",
    port: int = 9086,
    allow_public: bool = False,
    name: str | None = None,
    description: str | None = None,
    card_path: str | None = None,
    streaming: bool = False,
    workers: int = 4,
) -> None:
    """Start the WORKAGENT A2A server."""
    prepare_hermes_home()
    start_workagent_mcp_bootstrap()

    if not is_loopback_host(host) and not allow_public:
        raise SystemExit(
            "Refusing non-loopback bind without --insecure. "
            "Use --insecure to intentionally expose WORKAGENT A2A on the network."
        )

    settings = load_workagent_settings(
        host=host,
        port=port,
        open_browser=False,
        allow_public=allow_public,
        embedded_chat=False,
        dist_dir=None,
    )
    if getattr(settings, "a2a_auth_enabled", False):
        if getattr(settings, "a2a_token_source", "disabled") == "generated":
            print("A2A session token (generated for this process):")
            print(getattr(settings, "a2a_session_token", ""))
        elif getattr(settings, "a2a_token_source", "disabled") == "env":
            print("A2A session token source: A2A_SESSION_TOKEN")
    app = create_a2a_app(
        settings,
        name=name,
        description=description,
        card_path=card_path,
        streaming=streaming,
        workers=workers,
    )
    uvicorn.run(app, host=host, port=port, log_level="warning", proxy_headers=False)
