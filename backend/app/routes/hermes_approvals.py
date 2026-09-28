"""Aprovações remotas (seção 6.4 da spec de integração): o Hermes local
declara que uma ação precisa de aprovação; o usuário decide pelo RIA."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.types.json import Json
from pydantic import BaseModel, Field

from app.auth import require
from app.db import get_connection
from app.device_auth import require_device
from app.hermes_service import audit

router = APIRouter(prefix="/api/hermes/approvals", tags=["hermes"])


class ApprovalIn(BaseModel):
    session_id: str
    command_id: str | None = None
    title: str = Field(min_length=1, max_length=300)
    payload: dict = Field(default_factory=dict)


class DecisionIn(BaseModel):
    decision: str  # approved | rejected
    justification: str | None = Field(default=None, max_length=2000)


def _serialize(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "session_id": str(row["session_id"]),
        "command_id": str(row["command_id"]) if row["command_id"] else None,
        "title": row["title"],
        "payload": row["payload"],
        "status": row["status"],
        "decided_by": str(row["decided_by"]) if row["decided_by"] else None,
        "decided_at": row["decided_at"].isoformat() if row["decided_at"] else None,
        "justification": row["justification"],
        "created_at": row["created_at"].isoformat(),
    }


def _approval_for_user(conn, approval_id: str, user: dict) -> dict:
    row = conn.execute(
        "SELECT * FROM hermes_approvals WHERE id = %s", (approval_id,)
    ).fetchone()
    if row is None or (
        not user["is_master"] and str(row["tenant_id"]) != str(user["tenant_id"])
    ):
        raise HTTPException(status_code=404, detail="Solicitação de aprovação não encontrada")
    return row


# ---------------------------------------------------------------- dispositivo


@router.post("", status_code=201)
def request_approval(payload: ApprovalIn, device: dict = Depends(require_device)):
    with get_connection() as conn:
        session = conn.execute(
            "SELECT * FROM hermes_sessions WHERE id = %s AND device_id = %s",
            (payload.session_id, device["id"]),
        ).fetchone()
        if session is None:
            raise HTTPException(status_code=404, detail="Sessão não encontrada")
        row = conn.execute(
            """INSERT INTO hermes_approvals (tenant_id, session_id, command_id, title, payload)
               VALUES (%s, %s, %s, %s, %s) RETURNING *""",
            (
                device["tenant_id"],
                payload.session_id,
                payload.command_id,
                payload.title,
                Json(payload.payload),
            ),
        ).fetchone()
        conn.execute(
            """UPDATE hermes_sessions SET status = 'waiting_approval', updated_at = now()
                WHERE id = %s""",
            (payload.session_id,),
        )
    return _serialize(row)


@router.get("/{approval_id}")
def get_approval_status(approval_id: str, device: dict = Depends(require_device)):
    """O dispositivo consulta o veredito (poll). Sem Relay ainda, é assim que
    a extensão sabe se pode prosseguir."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM hermes_approvals WHERE id = %s AND tenant_id = %s",
            (approval_id, device["tenant_id"]),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Solicitação de aprovação não encontrada")
    return _serialize(row)


# ---------------------------------------------------------------- usuário


@router.get("")
def list_approvals(
    session_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(require("hermes", "view")),
):
    clauses, params = [], []
    if not user["is_master"]:
        clauses.append("tenant_id = %s")
        params.append(user["tenant_id"])
    if session_id:
        clauses.append("session_id = %s")
        params.append(session_id)
    if status:
        clauses.append("status = %s")
        params.append(status)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM hermes_approvals{where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (*params, limit, offset),
        ).fetchall()
    return [_serialize(r) for r in rows]


@router.post("/{approval_id}/decide")
def decide_approval(
    approval_id: str, payload: DecisionIn, user: dict = Depends(require("hermes", "edit"))
):
    if payload.decision not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="Decisão inválida")
    with get_connection() as conn:
        approval = _approval_for_user(conn, approval_id, user)
        if approval["status"] != "pending":
            raise HTTPException(
                status_code=409, detail=f"Solicitação já decidida: {approval['status']}"
            )
        row = conn.execute(
            """UPDATE hermes_approvals
                  SET status = %s, decided_by = %s, decided_at = %s, justification = %s
                WHERE id = %s RETURNING *""",
            (payload.decision, user["id"], datetime.now(UTC), payload.justification, approval_id),
        ).fetchone()
        audit(
            conn,
            tenant_id=approval["tenant_id"],
            actor_type="user",
            actor_id=str(user["id"]),
            action=f"hermes.approval.{payload.decision}",
            resource_type="hermes_approval",
            resource_id=approval_id,
            metadata={"justification": payload.justification} if payload.justification else {},
        )
    return _serialize(row)
