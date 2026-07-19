from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from app import providers
from app.config import settings
from app.graph import close_graph
from app.runs import router as runs_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await close_graph()


app = FastAPI(title="agent-platform kernel", lifespan=lifespan)
app.include_router(runs_router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "kernel"}


class StubScriptIn(BaseModel):
    rules: list[tuple[str, str]] = []
    default: str = "ok"


if settings.enable_stub_control:

    @app.post("/stub/script")
    def set_stub_script(payload: StubScriptIn):
        """Test-only: program the deterministic stub provider."""
        providers.stub_script = providers.StubScript(payload.rules, payload.default)
        return {"status": "ok"}
