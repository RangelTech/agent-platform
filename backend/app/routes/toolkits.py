"""Tool catalog for the template editor (proxied from the kernel's MCP
list_tools) and the per-conversation tool-call trace."""

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.auth import current_user, require
from app.config import settings
from app.db import get_connection

router = APIRouter(prefix="/api", tags=["toolkits"])


async def _kernel_headers() -> dict:
    if settings.kernel_audience:
        from app.gcp_auth import id_token_for

        return {"Authorization": f"Bearer {await id_token_for(settings.kernel_audience)}"}
    if settings.kernel_internal_token:
        return {"Authorization": f"Bearer {settings.kernel_internal_token}"}
    return {}


@router.get("/toolkits")
async def list_toolkits(user: dict = Depends(require("templates", "view"))):
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{settings.kernel_url}/v1/tools", headers=await _kernel_headers()
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Kernel indisponível: {exc}") from exc


@router.get("/chats/{chat_id}/tool-calls")
def list_tool_calls(chat_id: str, user: dict = Depends(current_user)):
    with get_connection() as conn:
        chat = conn.execute(
            "SELECT id FROM chats WHERE id = %s AND user_id = %s",
            (chat_id, user["id"]),
        ).fetchone()
        if chat is None:
            raise HTTPException(status_code=404, detail="Conversa não encontrada")
        rows = conn.execute(
            """SELECT agent_name, tool_name, input, output, status, duration_ms, created_at
                 FROM tool_calls WHERE chat_id = %s ORDER BY created_at""",
            (chat_id,),
        ).fetchall()
    return [
        {
            "agent": r["agent_name"],
            "tool": r["tool_name"],
            "input": r["input"],
            "output": r["output"],
            "status": r["status"],
            "duration_ms": r["duration_ms"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]
