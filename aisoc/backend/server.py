"""AISOC backend server entrypoint."""

from __future__ import annotations

from pathlib import Path
import webbrowser

from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from aisoc.backend.agent_runtime import prepare_hermes_home
from aisoc.backend.auth import require_authenticated_user
from aisoc.backend.chat import ChatSessionManager, build_agent_chat_router
from aisoc.backend.config import AisocSettings, is_loopback_host, load_aisoc_settings
from aisoc.backend.routes.auth import build_auth_router
from aisoc.backend.routes.cron import build_cron_router
from aisoc.backend.routes.logs import build_logs_router
from aisoc.backend.routes.mcp_servers import build_mcp_servers_router
from aisoc.backend.routes.memory import build_memory_router
from aisoc.backend.routes.ontology import build_ontology_router
from aisoc.backend.routes.overview import build_overview_router
from aisoc.backend.routes.sessions import build_sessions_router
from aisoc.backend.routes.skill_hub import build_skill_hub_router
from aisoc.backend.routes.skills import build_skills_router
from aisoc.backend.routes.sso import build_sso_router
from aisoc.backend.routes.kb import build_kb_router
from aisoc.backend.routes.system import build_system_router
from aisoc.backend.routes.users import build_users_router
from aisoc.backend.services.quick_command_service import QuickCommandService
from aisoc.backend.services.skill_installer import bundled_skills_root, install_bundled_skills
from aisoc.backend.services.user_service import UserService


PUBLIC_API_PATHS = frozenset(
    {
        "/api/auth/login",
        "/api/auth/register",
        "/api/auth/session",
        "/api/auth/logout",
        "/api/sso/start",
        "/api/sso/callback",
        "/api/sso/exchange",
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
                "AISOC backend API. Use the `Authorize` button in Swagger UI "
                "and provide `Bearer <token>` automatically via the JWT access token field."
            ),
            routes=app.routes,
        )

        components = schema.setdefault("components", {})
        security_schemes = components.setdefault("securitySchemes", {})
        security_schemes["bearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": (
                "Paste AISOC JWT access token. Swagger will send "
                "`Authorization: Bearer <token>`."
            ),
        }
        schema["security"] = [{"bearerAuth": []}]

        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi


def create_app(settings: AisocSettings | None = None) -> FastAPI:
    """Create the AISOC FastAPI application."""
    active_settings = settings or load_aisoc_settings()
    app = FastAPI(title="AISOC Backend")
    app.state.aisoc_settings = active_settings
    user_service = UserService()
    admin_ready = user_service.ensure_bootstrap_admin()
    app.state.user_service = user_service
    app.state.admin_setup_required = not admin_ready

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
        if request.method != "OPTIONS" and path.startswith("/api/") and path not in PUBLIC_API_PATHS:
            try:
                require_authenticated_user(request, active_settings, user_service)
            except Exception as exc:
                # Keep middleware response stable and explicit.
                detail = getattr(exc, "detail", "Unauthorized")
                return JSONResponse(status_code=401, content={"detail": detail})
        return await call_next(request)

    chat_manager = ChatSessionManager()
    quick_command_service = QuickCommandService()
    app.state.chat_manager = chat_manager
    app.state.quick_command_service = quick_command_service

    app.include_router(build_auth_router(active_settings, user_service))
    app.include_router(build_sso_router(active_settings, user_service, user_service.store))
    app.include_router(build_users_router(active_settings, user_service))
    app.include_router(
        build_system_router(active_settings, user_service, admin_setup_required=not admin_ready)
    )
    app.include_router(
        build_agent_chat_router(
            active_settings,
            user_service,
            manager=chat_manager,
            quick_command_service=quick_command_service,
        )
    )
    app.include_router(build_sessions_router(active_settings, user_service))
    app.include_router(build_cron_router())
    app.include_router(build_skills_router())
    app.include_router(build_mcp_servers_router())
    app.include_router(build_skill_hub_router())
    app.include_router(build_memory_router())
    app.include_router(build_logs_router())
    app.include_router(build_overview_router())
    app.include_router(build_kb_router())
    app.include_router(build_ontology_router())
    _install_docs_bearer_auth(app)

    # Agent2UI bridge + template assets. Mounted outside /api so the auth
    # middleware does not intercept it: deliverable HTML rendered in the
    # sandboxed preview iframe loads /agent2ui/agent2ui-bridge.js anonymously.
    agent2ui_assets = bundled_skills_root() / "html-deliverable" / "assets"
    if agent2ui_assets.is_dir():
        app.mount("/agent2ui", StaticFiles(directory=agent2ui_assets), name="agent2ui")

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
) -> None:
    """Start the AISOC backend server."""
    prepare_hermes_home()

    installed_skills = install_bundled_skills()
    if installed_skills:
        print(f"Installed bundled skills into HERMES_HOME: {', '.join(installed_skills)}")

    if not is_loopback_host(host) and not allow_public:
        raise SystemExit(
            "Refusing non-loopback bind without --insecure. "
            "Use --insecure to intentionally expose AISOC on the network."
        )

    dist_dir = Path(__file__).resolve().parent / "web_dist"
    settings = load_aisoc_settings(
        host=host,
        port=port,
        open_browser=open_browser,
        allow_public=allow_public,
        dist_dir=dist_dir,
    )
    app = create_app(settings)

    if app.state.admin_setup_required:
        print(
            "AISOC requires initial admin setup. Set AISOC_BOOTSTRAP_ADMIN_PASSWORD "
            "and restart the service."
        )

    if open_browser:
        try:
            webbrowser.open(f"http://{host}:{port}/login")
        except Exception:
            pass

    uvicorn.run(app, host=host, port=port, log_level="warning", proxy_headers=False)
