"""WORKAGENT backend server entrypoint."""

from __future__ import annotations

from pathlib import Path
import webbrowser

from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from workagent.backend.agent_runtime import prepare_hermes_home
from workagent.backend.auth import verify_bearer_token
from workagent.backend.config import WorkagentSettings, is_loopback_host, load_workagent_settings
from workagent.backend.routes.auth import build_auth_router
from workagent.backend.routes.chat import build_chat_router
from workagent.backend.routes.cron import build_cron_router
from workagent.backend.routes.logs import build_logs_router
from workagent.backend.routes.memory import build_memory_router
from workagent.backend.routes.ontology import build_ontology_router
from workagent.backend.routes.overview import build_overview_router
from workagent.backend.routes.sessions import build_sessions_router
from workagent.backend.routes.skills import build_skills_router
from workagent.backend.routes.kb import build_kb_router
from workagent.backend.routes.system import build_system_router


PUBLIC_API_PATHS = frozenset(
    {
        "/api/auth/login",
        "/api/auth/session",
        "/api/auth/logout",
        "/health",
        "/api/system/bootstrap",
    }
)


def _install_docs_bearer_auth(app: FastAPI) -> None:
    """Add Swagger/OpenAPI bearer auth so docs can call protected APIs."""

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=app.title,
            version="0.1.0",
            description=(
                "WORKAGENT backend API. Use the `Authorize` button in Swagger UI "
                "and provide `Bearer <token>` automatically via the token field."
            ),
            routes=app.routes,
        )

        components = schema.setdefault("components", {})
        security_schemes = components.setdefault("securitySchemes", {})
        security_schemes["bearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "Token",
            "description": (
                "Paste WORKAGENT session token. Swagger will send "
                "`Authorization: Bearer <token>`."
            ),
        }
        schema["security"] = [{"bearerAuth": []}]

        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi


def create_app(settings: WorkagentSettings | None = None) -> FastAPI:
    """Create the WORKAGENT FastAPI application."""
    active_settings = settings or load_workagent_settings()
    app = FastAPI(title="WORKAGENT Backend")
    app.state.workagent_settings = active_settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1",
            "http://localhost",
            "http://127.0.0.1:9120",
            "http://localhost:9120",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and path not in PUBLIC_API_PATHS:
            try:
                verify_bearer_token(request, active_settings)
            except Exception as exc:
                # Keep middleware response stable and explicit.
                detail = getattr(exc, "detail", "Unauthorized")
                return JSONResponse(status_code=401, content={"detail": detail})
        return await call_next(request)

    app.include_router(build_auth_router(active_settings))
    app.include_router(build_system_router(active_settings))
    app.include_router(build_chat_router(active_settings))
    app.include_router(build_sessions_router())
    app.include_router(build_cron_router())
    app.include_router(build_skills_router())
    app.include_router(build_memory_router())
    app.include_router(build_logs_router())
    app.include_router(build_overview_router())
    app.include_router(build_kb_router())
    app.include_router(build_ontology_router())
    _install_docs_bearer_auth(app)

    dist_index = None
    dist_root = active_settings.dist_dir
    if active_settings.dist_dir:
        candidate = active_settings.dist_dir / "index.html"
        if candidate.exists():
            dist_index = candidate
        assets_dir = active_settings.dist_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    if dist_index is not None:

        @app.get("/", include_in_schema=False)
        async def root_index():
            return FileResponse(dist_index)

        @app.get("/{front_path:path}", include_in_schema=False)
        async def spa_fallback(front_path: str):
            if front_path.startswith("api/") or front_path == "health":
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            # Serve real static files from web_dist root (favicon/logo/manifest/etc.)
            # before falling back to SPA index for client-side routing.
            if dist_root:
                candidate = (dist_root / front_path).resolve()
                try:
                    candidate.relative_to(dist_root.resolve())
                except ValueError:
                    candidate = None
                if candidate and candidate.is_file():
                    return FileResponse(candidate)
            return FileResponse(dist_index)

    return app


def start_server(
    host: str = "127.0.0.1",
    port: int = 9120,
    open_browser: bool = True,
    allow_public: bool = False,
    embedded_chat: bool = False,
) -> None:
    """Start the WORKAGENT backend server."""
    prepare_hermes_home()

    if not is_loopback_host(host) and not allow_public:
        raise SystemExit(
            "Refusing non-loopback bind without --insecure. "
            "Use --insecure to intentionally expose WORKAGENT on the network."
        )

    dist_dir = Path(__file__).resolve().parent / "web_dist"
    settings = load_workagent_settings(
        host=host,
        port=port,
        open_browser=open_browser,
        allow_public=allow_public,
        embedded_chat=embedded_chat,
        dist_dir=dist_dir,
    )
    app = create_app(settings)

    if settings.token_source == "generated":
        print("WORKAGENT session token (generated for this process):")
        print(settings.session_token)
    else:
        print("WORKAGENT session token source: WORKAGENT_SESSION_TOKEN")

    if open_browser:
        try:
            webbrowser.open(f"http://{host}:{port}/login")
        except Exception:
            pass

    uvicorn.run(app, host=host, port=port, log_level="warning", proxy_headers=False)
