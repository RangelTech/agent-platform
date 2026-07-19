import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.migrations import run_migrations

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
    except Exception:
        logger.exception("migration run failed — continuing so /health can report")
    yield


app = FastAPI(title="agent-platform backend", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "service": "backend"}


static_dir = _resolve_static_dir()
if static_dir:
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        """SPA catch-all: serve real files if present, else index.html."""
        candidate = static_dir / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(static_dir / "index.html")
else:

    @app.get("/")
    def placeholder():
        return JSONResponse(
            {"service": "backend", "hint": "frontend build not found — run npm run build"}
        )
