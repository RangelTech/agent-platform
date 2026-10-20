"""Execuções do tenant (tela Execuções): agente, estado e duração de cada run."""

from fastapi import APIRouter, Depends, Query

from app.auth import require
from app.db import get_connection
from app.permissions import has_permission

router = APIRouter(prefix="/api", tags=["runs"])


@router.get("/runs")
def list_runs(
    page: int = Query(default=1, ge=1, le=10000),
    per_page: int = Query(default=25, ge=1, le=100),
    user: dict = Depends(require("hermes", "view")),
):
    """Escopo: o tenant do chamador; quem vê o uso da empresa vê todas, os demais só as próprias."""
    where, params = [], []
    if not user["is_master"]:
        where.append("r.tenant_id = %s")
        params.append(user["tenant_id"])
        if not has_permission(user, "usage", "view"):
            where.append("r.user_id = %s")
            params.append(user["id"])
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with get_connection() as conn:
        total = conn.execute(
            f"SELECT count(*) AS n FROM chat_runs r {clause}", tuple(params)
        ).fetchone()["n"]
        rows = conn.execute(
            f"""SELECT r.run_id, r.status, r.kernel, r.created_at, r.finished_at,
                       t.name AS agent_name, u.name AS user_name
                  FROM chat_runs r
                  LEFT JOIN templates t ON t.id = r.template_id
                  LEFT JOIN users u ON u.id = r.user_id
                  {clause}
                 ORDER BY r.created_at DESC
                 LIMIT %s OFFSET %s""",
            (*params, per_page, (page - 1) * per_page),
        ).fetchall()
    return {
        "items": [
            {
                "id": str(r["run_id"]),
                "agent": r["agent_name"],
                "user": r["user_name"],
                "status": r["status"],
                "started_at": r["created_at"].isoformat(),
                "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
                "duration_ms": int((r["finished_at"] - r["created_at"]).total_seconds() * 1000)
                if r["finished_at"]
                else None,
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }
