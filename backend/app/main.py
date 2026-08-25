import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from app.bootstrap import bootstrap_master
from app.config import settings
from app.migrations import run_migrations
from app.ragentes_guide import ensure_all_tenants
from app.routes import ai_router as ai_router_routes
from app.routes import ai_services as ai_service_routes
from app.routes import artifacts as artifact_routes
from app.routes import auth as auth_routes
from app.routes import chats as chat_routes
from app.routes import custom_tools as custom_tool_routes
from app.routes import datasources as datasource_routes
from app.routes import email_accounts as email_account_routes
from app.routes import files as file_routes
from app.routes import google_accounts as google_account_routes
from app.routes import installation_secrets as installation_secret_routes
from app.routes import integrations as integration_routes
from app.routes import mcp_store as mcp_store_routes
from app.routes import memories as memory_routes
from app.routes import omnichannel as omnichannel_routes
from app.routes import payments as payment_routes
from app.routes import profiles as profile_routes
from app.routes import public_api as public_routes
from app.routes import secrets as secret_routes
from app.routes import templates as template_routes
from app.routes import tenant_guide as tenant_guide_routes
from app.routes import tenants as tenant_routes
from app.routes import toolkits as toolkit_routes
from app.routes import usage as usage_routes
from app.routes import users as user_routes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOOT_PENDING = {
    "boot_ok": False,
    "migrations_ok": False,
    "detail": "startup has not run",
}


def _resolve_static_dir() -> Path | None:
    if settings.static_dir:
        p = Path(settings.static_dir)
        return p if p.is_dir() else None
    # Dev fallback: sibling frontend build.
    p = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    return p if p.is_dir() else None


def _boot_status(application: FastAPI) -> dict:
    return getattr(application.state, "boot_status", BOOT_PENDING.copy())


def _sanitize_error(exc: Exception) -> str:
    text = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
    return f"{exc.__class__.__name__}: {text}"[:500]


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.boot_status = BOOT_PENDING.copy()
    try:
        applied = run_migrations()
        if applied:
            logger.info("migrations applied: %s", applied)
        app.state.boot_status = {
            "boot_ok": False,
            "migrations_ok": True,
            "detail": "migrations applied" if applied else "migrations already current",
        }
    except Exception as exc:
        detail = _sanitize_error(exc)
        app.state.boot_status = {
            "boot_ok": False,
            "migrations_ok": False,
            "detail": detail,
        }
        logger.exception("MIGRATION_FAILED %s", detail)
    else:
        try:
            bootstrap_master()
            ensure_all_tenants()
        except Exception as exc:
            detail = _sanitize_error(exc)
            app.state.boot_status = {
                "boot_ok": False,
                "migrations_ok": True,
                "detail": detail,
            }
            logger.exception("BOOT_FAILED %s", detail)
        else:
            app.state.boot_status = {
                "boot_ok": True,
                "migrations_ok": True,
                "detail": "ready",
            }
    yield


app = FastAPI(title="agent-platform backend", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "service": "backend"}


@app.get("/health/ready")
def health_ready():
    status = _boot_status(app)
    if status["boot_ok"] and status["migrations_ok"]:
        return {"status": "ready", "service": "backend"}
    # O motivo fica no log, não no corpo. Este endpoint é público (o backend
    # sobe com --allow-unauthenticated) e o `detail` carrega a primeira linha da
    # exceção: numa falha real do psycopg isso é "connection to server at
    # <host>, port <porta> failed: ... for user <usuário>". Não vaza senha, mas
    # entrega host, porta e usuário do banco a quem só chamou uma URL.
    logger.error("READINESS_NOT_READY %s", status["detail"])
    return JSONResponse(
        status_code=503,
        content={
            "status": "not_ready",
            "service": "backend",
            "migrations_ok": status["migrations_ok"],
        },
    )


# API routers are registered here, before the SPA fallback below. Route order
# matters: the catch-all must stay last or it would shadow every /api route.
app.include_router(auth_routes.router)
app.include_router(tenant_routes.router)
app.include_router(profile_routes.router)
app.include_router(user_routes.router)
app.include_router(chat_routes.router)
app.include_router(custom_tool_routes.router)
app.include_router(ai_service_routes.router)
app.include_router(template_routes.router)
app.include_router(tenant_guide_routes.router)
app.include_router(secret_routes.router)
app.include_router(datasource_routes.router)
app.include_router(artifact_routes.router)
app.include_router(file_routes.router)
app.include_router(memory_routes.router)
app.include_router(usage_routes.router)
app.include_router(integration_routes.router)
app.include_router(public_routes.router)
app.include_router(toolkit_routes.router)
app.include_router(payment_routes.router)
app.include_router(email_account_routes.router)
app.include_router(google_account_routes.router)
app.include_router(mcp_store_routes.router)
app.include_router(omnichannel_routes.router)
app.include_router(ai_router_routes.router)
app.include_router(installation_secret_routes.router)


def _mount_spa(application: FastAPI) -> None:
    """Register the SPA fallback. Must be called after every API router."""
    static_dir = _resolve_static_dir()

    if static_dir is None:

        @application.get("/")
        def placeholder():
            return JSONResponse(
                {
                    "service": "backend",
                    "hint": "frontend build not found - run npm run build",
                }
            )

        return

    root = static_dir.resolve()
    index = root / "index.html"

    @application.get("/{full_path:path}")
    def spa(full_path: str):
        """Serve a real build file when it exists, else index.html so the SPA
        can route client-side. Paths are confined to the build directory."""
        if full_path:
            candidate = (root / full_path).resolve()
            # Confinement check: blocks traversal such as ../../etc/passwd
            if candidate.is_file() and candidate.is_relative_to(root):
                return FileResponse(candidate)
        return FileResponse(index)


_mount_spa(app)
