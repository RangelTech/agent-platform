"""Trilha de auditoria pesquisável (critério de aceite 15 da spec de
integração): quem fez o quê, de qual dispositivo/sessão, com qual
resultado."""

from fastapi import APIRouter, Depends, Query

from app.auth import require
from app.db import get_connection

router = APIRouter(prefix="/api/hermes/audit", tags=["hermes"])


def _serialize(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "actor_type": row["actor_type"],
        "actor_id": row["actor_id"],
        "action": row["action"],
        "resource_type": row["resource_type"],
        "resource_id": str(row["resource_id"]) if row["resource_id"] else None,
        "result": row["result"],
        "metadata": row["metadata"],
        "created_at": row["created_at"].isoformat(),
    }


@router.get("")
def list_audit_log(
    resource_type: str | None = None,
    resource_id: str | None = None,
    action: str | None = None,
    actor_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(require("hermes", "view")),
):
    clauses, params = [], []
    if not user["is_master"]:
        clauses.append("tenant_id = %s")
        params.append(user["tenant_id"])
    if resource_type:
        clauses.append("resource_type = %s")
        params.append(resource_type)
    if resource_id:
        clauses.append("resource_id = %s")
        params.append(resource_id)
    if action:
        clauses.append("action = %s")
        params.append(action)
    if actor_type:
        clauses.append("actor_type = %s")
        params.append(actor_type)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM hermes_audit_log{where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (*params, limit, offset),
        ).fetchall()
    return [_serialize(r) for r in rows]
