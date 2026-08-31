"""Aegis backend server entrypoint."""

from __future__ import annotations

import os
from pathlib import Path
import webbrowser

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from aegis.backend.auth import verify_bearer_token
from aegis.backend.chat.routes import build_chat_router
from aegis.backend.chat.service import ChatSessionManager
from aegis.backend.services.chat_quick_command_service import ChatQuickCommandService
from aegis.backend.config import AegisSettings, is_loopback_host, load_aegis_settings
from aegis.backend.routes.agents import build_agents_router
from aegis.backend.routes.a2a_context import build_a2a_context_router
from aegis.backend.routes.app_entries import build_app_entries_router
from aegis.backend.routes.audit import build_audit_router
from aegis.backend.routes.auth import build_auth_router
from aegis.backend.routes.lark import build_lark_sso_router
from aegis.backend.routes.overview import build_overview_router
from aegis.backend.routes.prompt_templates import build_prompt_templates_router
from aegis.backend.routes.routing import build_routing_router
from aegis.backend.routes.sso import build_sso_router
from aegis.backend.routes.system import build_system_router
from aegis.backend.routes.system_instructs import build_system_instructs_router
from aegis.backend.routes.skills_static import build_skills_static_router
from aegis.backend.routes.user_manuals import build_user_manuals_router
from aegis.backend.routes.users import build_users_router
from aegis.backend.services.user_service import UserService
from aegis.backend.services.a2a_context_service import A2AContextService
from aegis.backend.services.agent_service import AgentService
from aegis.backend.services.prompt_template_service import PromptTemplateService
from aegis.backend.services.prompt_template_store import PromptTemplateStore
from aegis.backend.services.user_manual_service import UserManualService
from aegis.backend.services.system_instruct_service import SystemInstructService
from aegis.backend.services.system_instruct_store import SystemInstructStore


PUBLIC_API_PATHS = frozenset(
    {
        "/api/auth/login",
        "/api/auth/register",
        "/api/auth/session",
        "/api/auth/logout",
        "/api/sso/start",
        "/api/sso/callback",
        "/api/sso/exchange",
        "/api/lark/start",
        "/api/lark/callback",
        "/health",
        "/api/system/bootstrap",
    }
)


def prepare_hermes_home() -> None:
    """Bind Aegis to the Hermes home of the agent that launched it."""
    try:
        from hermes_constants import get_hermes_home

        hermes_home = get_hermes_home()
        print(f"Using Hermes home: {hermes_home}")
        os.chdir(hermes_home)
        os.environ["HERMES_HOME"] = str(hermes_home)
        os.environ["HOME"] = str(hermes_home / "home")
    except Exception as exc:
        print(f"Warning: Failed to set Aegis Hermes home: {exc}")


def _format_browser_host(host: str) -> str:
    """Return a host string safe to embed in an HTTP URL."""
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _install_docs_bearer_auth(app: FastAPI) -> None:
    """Add Swagger/OpenAPI bearer auth so docs can call protected APIs."""

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=app.title,
            version="0.1.0",
            description=(
                "Aegis backend API. Use the `Authorize` button in Swagger UI "
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
                "Paste Aegis JWT access token. Swagger will send "
                "`Authorization: Bearer <token>`."
            ),
        }
        schema["security"] = [{"bearerAuth": []}]

        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi


def create_app(settings: AegisSettings | None = None) -> FastAPI:
    """Create the Aegis FastAPI application."""
    active_settings = settings or load_aegis_settings()
    app = FastAPI(title="Aegis Backend")
    app.state.aegis_settings = active_settings
    user_service = UserService()
    prompt_template_service = PromptTemplateService(PromptTemplateStore())
    system_instruct_service = SystemInstructService(SystemInstructStore())
    agent_service = AgentService()
    user_manual_service = UserManualService()
    a2a_context_service = A2AContextService()
    admin_ready = user_service.ensure_bootstrap_admin()
    app.state.user_service = user_service
    app.state.admin_setup_required = not admin_ready
    chat_manager = ChatSessionManager()
    quick_command_service = ChatQuickCommandService(
        agent_service,
        prompt_template_service,
        system_instruct_service,
    )
    app.state.chat_manager = chat_manager
    app.state.quick_command_service = quick_command_service
    app.state.user_manual_service = user_manual_service
    app.state.system_instruct_service = system_instruct_service

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1",
            "http://localhost",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:9130",
            "http://localhost:3000",
            "http://localhost:9130",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        path = request.url.path
        if request.method != "OPTIONS" and path.startswith("/api/") and path not in PUBLIC_API_PATHS:
            try:
                verify_bearer_token(request, active_settings, user_service)
            except Exception as exc:
                detail = getattr(exc, "detail", "Unauthorized")
                return JSONResponse(status_code=401, content={"detail": detail})
        return await call_next(request)

    app.include_router(build_auth_router(active_settings, user_service))
    app.include_router(build_sso_router(active_settings, user_service, user_service.store))
    app.include_router(build_lark_sso_router(active_settings, user_service, user_service.store))
    app.include_router(build_overview_router(active_settings, user_service))
    app.include_router(build_users_router(active_settings, user_service))
    app.include_router(
        build_prompt_templates_router(
            active_settings,
            user_service,
            prompt_template_service,
            quick_command_service,
        )
    )
    app.include_router(
        build_system_instructs_router(
            active_settings,
            user_service,
            system_instruct_service,
            quick_command_service,
        )
    )
    app.include_router(build_user_manuals_router(active_settings, user_service, user_manual_service))
    app.include_router(build_app_entries_router(active_settings, user_service))
    app.include_router(build_a2a_context_router(active_settings, user_service, a2a_context_service))
    app.include_router(
        build_agents_router(
            active_settings,
            user_service,
            agent_service,
            quick_command_service,
        )
    )
    app.include_router(build_routing_router(active_settings, user_service))
    app.include_router(build_audit_router(active_settings, user_service))
    app.include_router(
        build_system_router(
            active_settings,
            user_service,
            admin_setup_required=not admin_ready,
        )
    )
    app.include_router(
        build_chat_router(
            active_settings,
            user_service,
            manager=chat_manager,
            agent_service=agent_service,
            prompt_template_service=prompt_template_service,
            system_instruct_service=system_instruct_service,
            quick_command_service=quick_command_service,
        )
    )
    app.include_router(build_skills_static_router())
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
    port: int = 9130,
    open_browser: bool = True,
    allow_public: bool = False,
) -> None:
    """Start the Aegis backend server."""
    prepare_hermes_home()

    if not is_loopback_host(host) and not allow_public:
        raise SystemExit(
            "Refusing non-loopback bind without --insecure. "
            "Use --insecure to intentionally expose Aegis on the network."
        )

    dist_dir = Path(__file__).resolve().parent / "web_dist"
    settings = load_aegis_settings(
        host=host,
        port=port,
        open_browser=open_browser,
        allow_public=allow_public,
        dist_dir=dist_dir,
    )
    app = create_app(settings)
    if app.state.admin_setup_required:
        print(
            "Aegis requires initial admin setup. Set AEGIS_BOOTSTRAP_ADMIN_PASSWORD "
            "and restart the service."
        )

    if open_browser:
        try:
            webbrowser.open(f"http://{_format_browser_host(host)}:{port}/login")
        except Exception:
            pass

    uvicorn.run(app, host=host, port=port, log_level="warning", proxy_headers=False)
