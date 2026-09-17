"""WORKAGENT A2A server entrypoint backed by the official A2A SDK."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import time
from _thread import interrupt_main as _interrupt_main

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.request_handlers.response_helpers import agent_card_to_dict
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_jsonrpc_routes,
)
from a2a.types import AgentCapabilities, AgentCard, AgentInterface
from a2a.utils.constants import PROTOCOL_VERSION_CURRENT, TransportProtocol
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import uvicorn

from workagent.backend.a2a_service import BoundedTaskStore, HermesA2AExecutor
from workagent.backend.agent_runtime import prepare_hermes_home, start_workagent_mcp_bootstrap
from workagent.backend.auth import (
    extract_bearer_token,
    token_matches_value,
    verify_bearer_token_value,
)
from workagent.backend.config import WorkagentSettings, is_loopback_host, load_workagent_settings
from hermes_self_restart import request_self_restart
from workagent.backend.a2a_service.interactions import (
    INTERACTION_EXTENSION_URI,
    INTERACTION_RESPONSE_SUFFIX,
    InteractionError,
    interaction_extension,
)

A2A_RPC_PATH = os.getenv("A2A_BASE_PATH", "/a2a")
A2A_AGENT_CARD_PATH = f"{A2A_RPC_PATH}/.well-known/agent-card.json"
A2A_INTERACTION_RESPONSE_PATH = f"{A2A_RPC_PATH}{INTERACTION_RESPONSE_SUFFIX}"
A2A_PUBLIC_PATHS = frozenset(
    {
        "/health",
        "/.well-known/agent-card.json",
        A2A_AGENT_CARD_PATH,
    }
)
A2A_MANAGEMENT_PATHS = frozenset(
    {
        "/man",
        "/man/api/auth",
        "/man/api/restart",
    }
)
print(f"A2A RPC path: {A2A_RPC_PATH}")

MANAGEMENT_TOKEN_TTL_SECONDS = 300
MANAGEMENT_UNAVAILABLE_DETAIL = "A2A management is not enabled"


class _ManagementAuthRequest(BaseModel):
    token: str


class _ManagementAccessTokens:
    """Process-local, short-lived bearer credentials for A2A management."""

    def __init__(self) -> None:
        self._tokens: dict[str, float] = {}

    def issue(self) -> str:
        now = time.monotonic()
        self._discard_expired(now)
        token = secrets.token_urlsafe(32)
        expires_at = now + MANAGEMENT_TOKEN_TTL_SECONDS
        self._tokens[token] = expires_at
        return token

    def validates(self, candidate: str | None) -> bool:
        now = time.monotonic()
        self._discard_expired(now)
        if not candidate:
            return False
        return any(
            token_matches_value(candidate, issued_token)
            for issued_token in self._tokens
        )

    def _discard_expired(self, now: float) -> None:
        self._tokens = {
            token: expires_at
            for token, expires_at in self._tokens.items()
            if expires_at > now
        }


def _graceful_shutdown() -> None:
    """Ask Python's main thread to stop after FastAPI sends the response."""
    _interrupt_main()


def _is_management_path(path: str) -> bool:
    return path in A2A_MANAGEMENT_PATHS


def _management_page(*, enabled: bool, nonce: str) -> str:
    enabled_js = "true" if enabled else "false"
    disabled = "" if enabled else " disabled"
    availability = (
        "Enter the dedicated management token to continue."
        if enabled
        else "A2A management is not enabled. Restart is unavailable."
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WORKAGENT A2A Management</title>
  <style nonce="{nonce}">
    :root {{ color-scheme: dark; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #101417; color: #d9e1e5; }}
    main {{ width: min(680px, calc(100% - 32px)); margin: 56px auto; border: 1px solid #465159; background: #171d21; }}
    header, section {{ padding: 22px; border-bottom: 1px solid #343e44; }}
    section:last-child {{ border-bottom: 0; }}
    h1, h2 {{ margin: 0 0 12px; font-size: 1rem; letter-spacing: .08em; text-transform: uppercase; }}
    p {{ line-height: 1.55; color: #aeb9bf; }}
    label {{ display: block; margin-bottom: 8px; }}
    input, button {{ width: 100%; min-height: 44px; border: 1px solid #59666e; border-radius: 0; font: inherit; }}
    input {{ padding: 10px; color: #eef3f5; background: #0e1215; }}
    button {{ margin-top: 12px; padding: 10px 14px; color: #101417; background: #c2d4dc; cursor: pointer; font-weight: 700; }}
    button.danger {{ color: #fff; background: #9f2f2f; border-color: #d35a5a; }}
    button:disabled, input:disabled {{ opacity: .42; cursor: not-allowed; }}
    #status {{ min-height: 24px; margin-bottom: 0; }}
    .tag {{ display: inline-block; padding: 3px 7px; border: 1px solid #59666e; color: #c2d4dc; }}
  </style>
</head>
<body>
<main>
  <header>
    <span class="tag">SERVICE / WORKAGENT-A2A</span>
    <h1>A2A Management Console</h1>
    <p>{availability}</p>
  </header>
  <section aria-labelledby="login-title">
    <h2 id="login-title">Management authentication</h2>
    <label for="admin-token">Admin token</label>
    <input id="admin-token" type="password" autocomplete="off" spellcheck="false"{disabled}>
    <button id="login-button" type="button"{disabled}>Authenticate</button>
  </section>
  <section aria-labelledby="restart-title">
    <h2 id="restart-title">Process control</h2>
    <p>Restart creates one replacement watcher and gracefully stops this process.</p>
    <button id="restart-button" class="danger" type="button" disabled>Restart A2A</button>
    <p id="status" role="status" aria-live="polite">Restart is locked.</p>
  </section>
</main>
<script nonce="{nonce}">
(() => {{
  "use strict";
  const MANAGEMENT_ENABLED = {enabled_js};
  const RESTART_PHRASE = "RESTART A2A";
  const adminToken = document.getElementById("admin-token");
  const loginButton = document.getElementById("login-button");
  const restartButton = document.getElementById("restart-button");
  const statusLine = document.getElementById("status");
  let accessToken = "";
  let reloadRequested = false;

  const setStatus = (message) => {{ statusLine.textContent = message; }};

  loginButton.addEventListener("click", async () => {{
    if (!MANAGEMENT_ENABLED) return;
    loginButton.disabled = true;
    try {{
      const response = await fetch("/man/api/auth", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ token: adminToken.value }})
      }});
      adminToken.value = "";
      if (!response.ok) throw new Error(response.status === 401 ? "Invalid management token." : "Management unavailable.");
      const payload = await response.json();
      accessToken = payload.access_token;
      restartButton.disabled = false;
      setStatus(`Authenticated. Access expires in ${{payload.expires_in}} seconds.`);
    }} catch (error) {{
      accessToken = "";
      restartButton.disabled = true;
      setStatus(error.message);
    }} finally {{
      loginButton.disabled = !MANAGEMENT_ENABLED;
    }}
  }});

  async function pollForReplacement(oldPid) {{
    let sawDowntime = false;
    while (!reloadRequested) {{
      try {{
        const response = await fetch("/health", {{ cache: "no-store" }});
        if (!response.ok) throw new Error("unhealthy");
        const health = await response.json();
        if (health.pid !== oldPid || (sawDowntime && health.pid == null)) {{
          reloadRequested = true;
          window.location.reload();
          return;
        }}
      }} catch (_error) {{
        sawDowntime = true;
      }}
      await new Promise(resolve => setTimeout(resolve, 750));
    }}
  }}

  restartButton.addEventListener("click", async () => {{
    if (!accessToken || restartButton.disabled) return;
    if (!window.confirm("Danger: restart the WORKAGENT A2A process? Active requests may be interrupted.")) return;
    const phrase = window.prompt(`Type ${{RESTART_PHRASE}} to confirm.`) || "";
    if (phrase !== RESTART_PHRASE) {{
      setStatus("Restart cancelled: confirmation phrase did not match.");
      return;
    }}
    restartButton.disabled = true;
    loginButton.disabled = true;
    adminToken.disabled = true;
    setStatus("Restart requested. Waiting for replacement process…");
    try {{
      const response = await fetch("/man/api/restart", {{
        method: "POST",
        headers: {{ "Authorization": `Bearer ${{accessToken}}` }}
      }});
      if (!response.ok) throw new Error(response.status === 401 ? "Management access expired. Reload to authenticate again." : "Restart unavailable.");
      const payload = await response.json();
      void pollForReplacement(payload.pid);
    }} catch (error) {{
      accessToken = "";
      setStatus(error.message);
      if (MANAGEMENT_ENABLED) {{
        loginButton.disabled = false;
        adminToken.disabled = false;
      }}
    }}
  }});
}})();
</script>
</body>
</html>"""


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
) -> FastAPI:
    """Create the WORKAGENT A2A FastAPI application."""
    del workers
    active_settings = settings or load_workagent_settings(open_browser=False)
    app = FastAPI(title="WORKAGENT A2A")
    app.state.workagent_settings = active_settings
    management_tokens = _ManagementAccessTokens()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if _is_management_path(request.url.path):
            origin = (request.headers.get("Origin") or "").strip()
            service_origin = f"{request.url.scheme}://{request.url.netloc}"
            if origin and origin != service_origin:
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "Cross-origin management requests are forbidden"},
                )
        if (
            active_settings.a2a_auth_enabled
            and request.url.path not in A2A_PUBLIC_PATHS
            and not _is_management_path(request.url.path)
        ):
            try:
                verify_bearer_token_value(request, active_settings.a2a_session_token)
            except Exception as exc:
                detail = getattr(exc, "detail", "Unauthorized")
                return JSONResponse(status_code=401, content={"detail": detail})
        return await call_next(request)

    @app.get("/health")
    async def health() -> dict[str, str | int]:
        return {"status": "ok", "module": "a2a", "pid": os.getpid()}

    @app.get("/man", response_class=HTMLResponse)
    async def management_page() -> HTMLResponse:
        nonce = secrets.token_urlsafe(24)
        content_security_policy = "; ".join(
            (
                "default-src 'none'",
                f"style-src 'nonce-{nonce}'",
                f"script-src 'nonce-{nonce}'",
                "connect-src 'self'",
                "frame-ancestors 'none'",
                "base-uri 'none'",
                "form-action 'none'",
            )
        )
        return HTMLResponse(
            _management_page(
                enabled=bool(active_settings.a2a_admin_token),
                nonce=nonce,
            ),
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": content_security_policy,
                "X-Frame-Options": "DENY",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )

    @app.post("/man/api/auth")
    async def management_auth(payload: _ManagementAuthRequest) -> JSONResponse:
        if not active_settings.a2a_admin_token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=MANAGEMENT_UNAVAILABLE_DETAIL,
            )
        if token_matches_value(payload.token, active_settings.a2a_session_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
        if not token_matches_value(payload.token, active_settings.a2a_admin_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
        access_token = management_tokens.issue()
        expires_at = time.time() + MANAGEMENT_TOKEN_TTL_SECONDS
        return JSONResponse(
            {
                "access_token": access_token,
                "token_type": "bearer",
                "expires_in": MANAGEMENT_TOKEN_TTL_SECONDS,
                "expires_at": int(expires_at),
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.post(
        "/man/api/restart",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def management_restart(
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> JSONResponse:
        if not active_settings.a2a_admin_token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=MANAGEMENT_UNAVAILABLE_DETAIL,
            )
        if not management_tokens.validates(extract_bearer_token(request)):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
        result = request_self_restart(
            "workagent-a2a",
            lambda: background_tasks.add_task(_graceful_shutdown),
        )
        return JSONResponse(
            result.as_dict(),
            status_code=status.HTTP_202_ACCEPTED,
            headers={"Cache-Control": "no-store"},
        )

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
                interaction_extension(A2A_INTERACTION_RESPONSE_PATH)
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

    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=[],
        jsonrpc_routes=create_jsonrpc_routes(request_handler, rpc_url=A2A_RPC_PATH),
    )
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
