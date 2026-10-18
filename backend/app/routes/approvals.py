"""Confirmação autenticada de ações sensíveis (dinheiro, envio, escrita, publicação).

O agente pede; quem decide é a pessoa, com a sessão dela. Só o dono da
conversa decide. A decisão é gravada aqui e repassada ao kernel de skills
(que também pode consultá-la pela rota interna).
"""

from __future__ import annotations

import logging
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import current_user
from app.db import get_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


class DecisionIn(BaseModel):
    decision: Literal["approve", "deny"]


def expire_stale(conn) -> None:
    conn.execute(
        "UPDATE approvals SET status = 'expired' WHERE status = 'pending' AND expires_at <= now()"
    )


def view(row: dict) -> dict:
    return {
        "approval_id": str(row["approval_id"]),
        "chat_id": row["chat_id"],
        "summary": row["summary"],
        "risk": row["risk"],
        "kind": row["kind"],
        "details": row["details"] or {},
        "expires_at": row["expires_at"].isoformat(),
    }


@router.get("/pending")
def pending(user: dict = Depends(current_user)):
    if user["tenant_id"] is None:
        return []
    with get_connection() as conn:
        expire_stale(conn)
        rows = conn.execute(
            """SELECT * FROM approvals
                WHERE tenant_id = %s AND user_id = %s AND status = 'pending'
                ORDER BY created_at""",
            (user["tenant_id"], user["id"]),
        ).fetchall()
    return [view(r) for r in rows]


async def _forward(approval: dict, decision: str) -> None:
    """Repassa a decisão ao kernel de skills; falha não desfaz a decisão
    (o kernel também consulta a rota interna)."""
    from app.kernel_router import kernel_headers, target_for_kind

    try:
        target = target_for_kind("skills")
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            await client.post(
                f"{target.url.rstrip('/')}/v1/approvals/{approval['approval_id']}",
                json={
                    "decision": decision,
                    "run_id": str(approval["run_id"]),
                    "decided_by": str(approval["decided_by"]),
                },
                headers=await kernel_headers(target),
            )
    except (httpx.HTTPError, HTTPException):
        logger.warning("approval decision could not be forwarded: %s", approval["approval_id"])


@router.post("/{approval_id}")
async def decide(approval_id: str, payload: DecisionIn, user: dict = Depends(current_user)):
    if user["tenant_id"] is None:
        raise HTTPException(status_code=404, detail="Aprovação não encontrada")
    with get_connection() as conn:
        expire_stale(conn)
        row = conn.execute(
            """SELECT * FROM approvals
                WHERE approval_id::text = %s AND tenant_id = %s AND user_id = %s""",
            (approval_id, user["tenant_id"], user["id"]),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Aprovação não encontrada")
        if row["status"] == "expired":
            return {"status": "expired"}
        if row["status"] != "pending":
            raise HTTPException(status_code=409, detail="Esta confirmação já foi respondida")
        new_status = "approved" if payload.decision == "approve" else "denied"
        row = conn.execute(
            """UPDATE approvals SET status = %s, decided_by = %s, decided_at = now()
                WHERE approval_id = %s AND status = 'pending' RETURNING *""",
            (new_status, user["id"], row["approval_id"]),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=409, detail="Esta confirmação já foi respondida")
        conn.execute(
            """INSERT INTO tool_audit (run_id, tenant_id, user_id, chat_id, tool, ok)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                row["run_id"],
                row["tenant_id"],
                user["id"],
                row["chat_id"],
                f"approval:{row['kind']}",
                new_status == "approved",
            ),
        )
    await _forward(row, payload.decision)
    return {"status": new_status}
