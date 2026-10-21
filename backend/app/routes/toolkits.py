"""Rastro de chamadas de ferramenta por conversa. (O catálogo `/api/toolkits` do
editor `graph` saiu com o kernel-llm: o catálogo dos agentes por skills vem de
`tool_catalog`.)"""

from fastapi import APIRouter, Depends, HTTPException

from app.auth import current_user
from app.db import get_connection
from app.permissions import require_scope_for_chatwoot

router = APIRouter(prefix="/api", tags=["toolkits"])


@router.get("/chats/{chat_id}/tool-calls")
def list_tool_calls(chat_id: str, user: dict = Depends(current_user)):
    require_scope_for_chatwoot(user, "ai.runs.view")
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
