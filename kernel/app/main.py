from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from app import providers
from app.config import settings
from app.graph import close_graph
from app.runs import require_internal_auth
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


class TestModelIn(BaseModel):
    provider: str
    model: str
    api_key: str | None = None
    api_base: str | None = None


@app.post("/v1/test-model", dependencies=[Depends(require_internal_auth)])
async def test_model(payload: TestModelIn):
    """One tiny completion to prove the credentials/model work."""
    from app.providers import ModelConfig, stream_completion

    config = ModelConfig(
        provider=payload.provider,
        model=payload.model,
        api_key=payload.api_key,
        api_base=payload.api_base,
        max_tokens=5,
    )
    try:
        async for _ in stream_completion(
            config, [{"role": "user", "content": "responda: ok"}]
        ):
            break  # first delta is proof enough
        return {"ok": True, "detail": ""}
    except Exception as exc:  # noqa: BLE001 — the point is reporting it
        return {"ok": False, "detail": str(exc)[:500]}


class StubScriptIn(BaseModel):
    rules: list[tuple[str, str]] = []
    default: str = "ok"


if settings.enable_stub_control:

    @app.post("/stub/script")
    def set_stub_script(payload: StubScriptIn):
        """Test-only: program the deterministic stub provider."""
        providers.stub_script = providers.StubScript(payload.rules, payload.default)
        return {"status": "ok"}
