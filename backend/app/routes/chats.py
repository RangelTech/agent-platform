"""Chat endpoints: listing, history and the streaming gateway to the kernel.

POST /api/chat/send proxies the kernel's SSE stream straight to the browser
and persists both sides of the exchange. Until the AI-services ticket lands,
the model comes from backend settings (stub by default, Gemini when a key is
configured).
"""

import json
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth import current_user
from app.config import settings
from app.db import get_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chats"])


class SendRequest(BaseModel):
    message: str = Field(min_length=1, max_length=32_000)
    chat_id: str | None = None


def _serialize_chat(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


@router.get("/chats")
def list_chats(user: dict = Depends(current_user)):
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM chats
                WHERE user_id = %s AND NOT is_hidden
                ORDER BY updated_at DESC""",
            (user["id"],),
        ).fetchall()
    return [_serialize_chat(r) for r in rows]


@router.get("/chats/{chat_id}/messages")
def list_messages(chat_id: str, user: dict = Depends(current_user)):
    with get_connection() as conn:
        chat = conn.execute(
            "SELECT id FROM chats WHERE id = %s AND user_id = %s",
            (chat_id, user["id"]),
        ).fetchone()
        if chat is None:
            raise HTTPException(status_code=404, detail="Conversa não encontrada")
        rows = conn.execute(
            """SELECT id, role, content, created_at FROM chat_messages
                WHERE chat_id = %s ORDER BY created_at""",
            (chat_id,),
        ).fetchall()
    return [
        {
            "id": str(r["id"]),
            "role": r["role"],
            "content": r["content"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


@router.put("/chats/{chat_id}/hide")
def hide_chat(chat_id: str, user: dict = Depends(current_user)):
    with get_connection() as conn:
        row = conn.execute(
            "UPDATE chats SET is_hidden = TRUE WHERE id = %s AND user_id = %s RETURNING id",
            (chat_id, user["id"]),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Conversa não encontrada")
    return {"status": "ok"}


def _ensure_chat(user: dict, payload: SendRequest) -> dict:
    if user["tenant_id"] is None:
        # The master administers the platform; conversations belong to tenant
        # users. This also guarantees chats always carry a tenant_id.
        raise HTTPException(
            status_code=400, detail="Usuário master não participa de conversas"
        )
    with get_connection() as conn:
        if payload.chat_id:
            chat = conn.execute(
                "SELECT * FROM chats WHERE id = %s AND user_id = %s",
                (payload.chat_id, user["id"]),
            ).fetchone()
            if chat is None:
                raise HTTPException(status_code=404, detail="Conversa não encontrada")
        else:
            title = payload.message[:60] + ("…" if len(payload.message) > 60 else "")
            chat = conn.execute(
                """INSERT INTO chats (tenant_id, user_id, title)
                   VALUES (%s, %s, %s) RETURNING *""",
                (user["tenant_id"], user["id"], title),
            ).fetchone()
        conn.execute(
            "INSERT INTO chat_messages (chat_id, role, content) VALUES (%s, 'user', %s)",
            (chat["id"], payload.message),
        )
        conn.execute("UPDATE chats SET updated_at = now() WHERE id = %s", (chat["id"],))
    return chat


def _model_spec() -> dict:
    """Interim model resolution (replaced by AI services in ticket 05)."""
    if settings.ai_provider == "stub" or not settings.ai_api_key:
        return {"provider": "stub", "model": "stub-1"}
    return {
        "provider": settings.ai_provider,
        "model": settings.ai_model,
        "api_key": settings.ai_api_key,
    }


@router.post("/chat/send")
async def send_message(payload: SendRequest, user: dict = Depends(current_user)):
    chat = _ensure_chat(user, payload)
    chat_id = str(chat["id"])

    kernel_payload = {
        "thread_id": chat_id,
        "message": payload.message,
        "model": _model_spec(),
        "system_prompt": settings.ai_system_prompt,
    }
    headers = {}
    if settings.kernel_audience:
        from app.gcp_auth import id_token_for

        headers["Authorization"] = f"Bearer {await id_token_for(settings.kernel_audience)}"
    elif settings.kernel_internal_token:
        headers["Authorization"] = f"Bearer {settings.kernel_internal_token}"

    async def relay():
        # chat_id first, so a brand-new conversation appears in the sidebar
        # before the first token arrives.
        yield f"event: chat\ndata: {json.dumps({'chat_id': chat_id})}\n\n"
        assistant_text = ""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(310.0)) as client:
                async with client.stream(
                    "POST",
                    f"{settings.kernel_url}/v1/runs",
                    json=kernel_payload,
                    headers=headers,
                ) as response:
                    if response.status_code != 200:
                        detail = (await response.aread()).decode()[:500]
                        yield (
                            "event: error\n"
                            f"data: {json.dumps({'detail': detail})}\n\n"
                        )
                        return
                    current_event = ""
                    async for line in response.aiter_lines():
                        # Pass the kernel's SSE through untouched; capture the
                        # `done` payload (the full reply) for persistence.
                        if line.startswith("event: "):
                            current_event = line[7:].strip()
                        elif line.startswith("data: ") and current_event == "done":
                            try:
                                assistant_text = json.loads(line[6:]).get("text", "")
                            except json.JSONDecodeError:
                                pass
                        yield line + "\n"
        except httpx.HTTPError as exc:
            logger.exception("kernel unreachable")
            yield f"event: error\ndata: {json.dumps({'detail': str(exc)})}\n\n"
            return
        if assistant_text:
            with get_connection() as conn:
                conn.execute(
                    """INSERT INTO chat_messages (chat_id, role, content)
                       VALUES (%s, 'assistant', %s)""",
                    (chat_id, assistant_text),
                )

    return StreamingResponse(
        relay(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
