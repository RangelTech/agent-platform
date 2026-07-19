"""POST /v1/runs — execute one conversation turn, streaming SSE.

Events:
  event: token   data: {"text": "..."}          (one per model delta)
  event: done    data: {"text": "<full reply>"}
  event: error   data: {"detail": "..."}

The turn is bounded by settings.turn_timeout_seconds; hitting it emits an
error event and stops the run. Client disconnects cancel the run cleanly —
no orphaned work survives the request.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.graph import get_graph

logger = logging.getLogger(__name__)

router = APIRouter(tags=["runs"])


class ModelSpec(BaseModel):
    provider: str
    model: str
    api_key: str | None = None
    api_base: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


class RunRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1)
    model: ModelSpec
    system_prompt: str = ""


def require_internal_auth(request: Request) -> None:
    """Service-to-service gate. In Cloud Run the platform enforces OIDC; this
    shared-token check covers dev and doubles as defense in depth."""
    if not settings.internal_token:
        return
    header = request.headers.get("authorization", "")
    if header != f"Bearer {settings.internal_token}":
        raise HTTPException(status_code=401, detail="unauthorized")


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/v1/runs", dependencies=[Depends(require_internal_auth)])
async def create_run(payload: RunRequest):
    graph = await get_graph()

    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()

        async def produce():
            final_text = ""
            try:
                async for mode, chunk in graph.astream(
                    {
                        "messages": [{"role": "user", "content": payload.message}],
                        "model": payload.model.model_dump(),
                        "system_prompt": payload.system_prompt,
                    },
                    config={"configurable": {"thread_id": payload.thread_id}},
                    stream_mode=["custom", "values"],
                ):
                    if mode == "custom" and chunk.get("type") == "token":
                        await queue.put(("token", {"text": chunk["text"]}))
                    elif mode == "values" and chunk.get("messages"):
                        final_text = chunk["messages"][-1].content
                await queue.put(("done", {"text": final_text}))
            except Exception as exc:  # noqa: BLE001 — reported to the client as an event
                logger.exception("run failed thread=%s", payload.thread_id)
                await queue.put(("error", {"detail": str(exc)}))
            finally:
                await queue.put(None)

        producer = asyncio.create_task(produce())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(
                        queue.get(), timeout=settings.turn_timeout_seconds
                    )
                except TimeoutError:
                    producer.cancel()
                    yield _sse("error", {"detail": "timeout"})
                    return
                if item is None:
                    return
                event, data = item
                yield _sse(event, data)
        finally:
            # Covers client disconnects too: StreamingResponse closes the
            # generator, and cancelling the producer kills the model call.
            producer.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
