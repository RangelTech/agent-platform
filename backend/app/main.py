import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from app.bootstrap import bootstrap_master
from app.config import settings
from app.migrations import run_migrations
from app.routes import ai_services as ai_service_routes
from app.routes import artifacts as artifact_routes
from app.routes import auth as auth_routes
from app.routes import chats as chat_routes
from app.routes import datasources as datasource_routes
from app.routes import profiles as profile_routes
from app.routes import secrets as secret_routes
from app.routes import templates as template_routes
from app.routes import tenants as tenant_routes
from app.routes import toolkits as toolkit_routes
from app.routes import users as user_routes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _resolve_static_dir() -> Path | None:
    if settings.static_dir:
        p = Path(settings.static_dir)
        return p if p.is_dir() else None
    # Dev fallback: sibling frontend build.
    p = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    return p if p.is_dir() else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        applied = run_migrations()
        if applied:
            logger.info("migrations applied: %s", applied)
        bootstrap_master()
    except Exception:
        logger.exception("boot tasks failed — continuing so /health can report")
    yield


app = FastAPI(title="agent-platform backend", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "service": "backend"}


# API routers are registered here, before the SPA fallback below. Route order
# matters: the catch-all must stay last or it would shadow every /api route.
app.include_router(auth_routes.router)
app.include_router(tenant_routes.router)
app.include_router(profile_routes.router)
app.include_router(user_routes.router)
app.include_router(chat_routes.router)
app.include_router(ai_service_routes.router)
app.include_router(template_routes.router)
app.include_router(secret_routes.router)
app.include_router(datasource_routes.router)
app.include_router(artifact_routes.router)
app.include_router(toolkit_routes.router)


def _mount_spa(application: FastAPI) -> None:
    """Register the SPA fallback. Must be called after every API router."""
    static_dir = _resolve_static_dir()

    if static_dir is None:

        @application.get("/")
        def placeholder():
            return JSONResponse(
                {
                    "service": "backend",
                    "hint": "frontend build not found — run npm run build",
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
