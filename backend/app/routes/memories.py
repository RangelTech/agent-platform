"""User-facing memory transparency: see and delete what the platform
remembers about you."""

from fastapi import APIRouter, Depends, HTTPException

from app.auth import current_user
from app.db import get_connection

router = APIRouter(prefix="/api/memories", tags=["memories"])


@router.get("")
def list_memories(user: dict = Depends(current_user)):
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, content, created_at FROM memories
                WHERE tenant_id = %s AND user_id = %s
                ORDER BY created_at DESC""",
            (user["tenant_id"], user["id"]),
        ).fetchall()
    return [
        {
            "id": str(r["id"]),
            "content": r["content"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


@router.delete("/{memory_id}")
def delete_memory(memory_id: str, user: dict = Depends(current_user)):
    with get_connection() as conn:
        row = conn.execute(
            """DELETE FROM memories
                WHERE id = %s AND tenant_id = %s AND user_id = %s RETURNING id""",
            (memory_id, user["tenant_id"], user["id"]),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Memória não encontrada")
    return {"status": "ok"}

# ------------------------------------------------- memórias propostas pelo agente ---


@router.get("/proposals")
def list_proposals(user: dict = Depends(current_user)):
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, content, status, created_at FROM memory_proposals
                WHERE tenant_id = %s AND user_id = %s AND status <> 'discarded'
                ORDER BY created_at DESC""",
            (user["tenant_id"], user["id"]),
        ).fetchall()
    return [
        {
            "id": str(r["id"]),
            "content": r["content"],
            "status": r["status"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


def _decide(proposal_id: str, user: dict, status: str) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            """UPDATE memory_proposals SET status = %s, updated_at = now()
                WHERE id::text = %s AND tenant_id = %s AND user_id = %s RETURNING id, status""",
            (status, proposal_id, user["tenant_id"], user["id"]),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Memória não encontrada")
    return {"id": str(row["id"]), "status": row["status"]}


@router.post("/proposals/{proposal_id}/approve")
def approve_proposal(proposal_id: str, user: dict = Depends(current_user)):
    return _decide(proposal_id, user, "approved")


@router.post("/proposals/{proposal_id}/discard")
def discard_proposal(proposal_id: str, user: dict = Depends(current_user)):
    return _decide(proposal_id, user, "discarded")
