"""Sessões Hermes: o dispositivo publica (upsert idempotente + eventos), o
usuário consulta (seção 6.2/6.3 e 7.4 da spec de integração)."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth import require
from app.db import get_connection
from app.device_auth import require_device
from app.hermes_service import append_session_event

router = APIRouter(prefix="/api/hermes/sessions", tags=["hermes"])

STATUSES = ("idle", "running", "waiting_approval", "completed", "failed", "ended")


class SessionUpsertIn(BaseModel):
    external_session_id: str = Field(min_length=1, max_length=200)
    title: str | None = None
    workspace_path: str | None = None
    repo: str | None = None
    branch: str | None = None
    provider_name: str | None = None
    model_name: str | None = None
    status: str = "idle"


class EventIn(BaseModel):
    sequence: int = Field(ge=0)
    type: str = Field(min_length=1, max_length=100)
    payload: dict = Field(default_factory=dict)
    occurred_at: str | None = None  # ISO 8601; ignorado se ausente (usa now())


def _serialize(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "device_id": str(row["device_id"]),
        "external_session_id": row["external_session_id"],
        "title": row["title"],
        "workspace_path": row["workspace_path"],
        "repo": row["repo"],
        "branch": row["branch"],
        "provider_name": row["provider_name"],
        "model_name": row["model_name"],
        "status": row["status"],
        "is_archived": row["is_archived"],
        "last_activity_at": row["last_activity_at"].isoformat(),
        "started_at": row["started_at"].isoformat(),
        "ended_at": row["ended_at"].isoformat() if row["ended_at"] else None,
        "updated_at": row["updated_at"].isoformat(),
    }


def _serialize_event(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "session_id": str(row["session_id"]),
        "sequence": row["sequence"],
        "type": row["type"],
        "payload": row["payload"],
        "occurred_at": row["occurred_at"].isoformat(),
    }


def _scoped_for_user(conn, session_id: str, user: dict) -> dict:
    row = conn.execute(
        "SELECT * FROM hermes_sessions WHERE id = %s", (session_id,)
    ).fetchone()
    if row is None or (
        not user["is_master"] and str(row["tenant_id"]) != str(user["tenant_id"])
    ):
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    return row


def _scoped_for_device(conn, session_id: str, device: dict) -> dict:
    row = conn.execute(
        "SELECT * FROM hermes_sessions WHERE id = %s", (session_id,)
    ).fetchone()
    if row is None or str(row["device_id"]) != device["id"]:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    return row


# ---------------------------------------------------------------- device


@router.post("", status_code=201)
def upsert_session(payload: SessionUpsertIn, device: dict = Depends(require_device)):
    if payload.status not in STATUSES:
        raise HTTPException(status_code=400, detail=f"Status inválido: {payload.status}")
    with get_connection() as conn:
        row = conn.execute(
            """INSERT INTO hermes_sessions
                   (tenant_id, device_id, external_session_id, title, workspace_path,
                    repo, branch, provider_name, model_name, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (device_id, external_session_id) DO UPDATE SET
                   title = EXCLUDED.title,
                   workspace_path = EXCLUDED.workspace_path,
                   repo = EXCLUDED.repo,
                   branch = EXCLUDED.branch,
                   provider_name = EXCLUDED.provider_name,
                   model_name = EXCLUDED.model_name,
                   status = EXCLUDED.status,
                   last_activity_at = now(),
                   updated_at = now(),
                   ended_at = CASE WHEN EXCLUDED.status = 'ended' THEN now()
                                    ELSE hermes_sessions.ended_at END
               RETURNING *""",
            (
                device["tenant_id"],
                device["id"],
                payload.external_session_id,
                payload.title,
                payload.workspace_path,
                payload.repo,
                payload.branch,
                payload.provider_name,
                payload.model_name,
                payload.status,
            ),
        ).fetchone()
    return _serialize(row)


@router.post("/{session_id}/events", status_code=201)
def publish_event(
    session_id: str, payload: EventIn, device: dict = Depends(require_device)
):
    with get_connection() as conn:
        session = _scoped_for_device(conn, session_id, device)
        row = append_session_event(
            conn,
            tenant_id=session["tenant_id"],
            session_id=session_id,
            sequence=payload.sequence,
            type_=payload.type,
            payload=payload.payload,
        )
    return _serialize_event(row)


# ---------------------------------------------------------------- usuário


@router.get("")
def list_sessions(
    device_id: str | None = None,
    status: str | None = None,
    include_archived: bool = False,
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(require("hermes", "view")),
):
    clauses, params = [], []
    if not user["is_master"]:
        clauses.append("s.tenant_id = %s")
        params.append(user["tenant_id"])
    if device_id:
        clauses.append("s.device_id = %s")
        params.append(device_id)
    if status:
        if status not in STATUSES:
            raise HTTPException(status_code=400, detail=f"Status inválido: {status}")
        clauses.append("s.status = %s")
        params.append(status)
    if not include_archived:
        clauses.append("NOT s.is_archived")
    if q:
        clauses.append(
            "(s.title ILIKE %s OR s.workspace_path ILIKE %s OR s.repo ILIKE %s)"
        )
        like = f"%{q}%"
        params.extend([like, like, like])
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_connection() as conn:
        rows = conn.execute(
            f"""SELECT s.*, d.name AS device_name, d.status AS device_status
                  FROM hermes_sessions s
                  JOIN hermes_devices d ON d.id = s.device_id
                {where}
                 ORDER BY s.last_activity_at DESC
                 LIMIT %s OFFSET %s""",
            (*params, limit, offset),
        ).fetchall()
    out = []
    for row in rows:
        item = _serialize(row)
        item["device_name"] = row["device_name"]
        item["device_status"] = row["device_status"]
        out.append(item)
    return out


@router.get("/{session_id}")
def get_session(session_id: str, user: dict = Depends(require("hermes", "view"))):
    with get_connection() as conn:
        row = _scoped_for_user(conn, session_id, user)
    return _serialize(row)


@router.get("/{session_id}/events")
def list_events(
    session_id: str,
    after_sequence: int = Query(default=-1, ge=-1),
    limit: int = Query(default=200, ge=1, le=1000),
    user: dict = Depends(require("hermes", "view")),
):
    with get_connection() as conn:
        _scoped_for_user(conn, session_id, user)
        rows = conn.execute(
            """SELECT * FROM hermes_session_events
                WHERE session_id = %s AND sequence > %s
                ORDER BY sequence ASC LIMIT %s""",
            (session_id, after_sequence, limit),
        ).fetchall()
    return [_serialize_event(r) for r in rows]


@router.post("/{session_id}/archive")
def archive_session(session_id: str, user: dict = Depends(require("hermes", "edit"))):
    with get_connection() as conn:
        _scoped_for_user(conn, session_id, user)
        row = conn.execute(
            """UPDATE hermes_sessions SET is_archived = TRUE, updated_at = now()
                WHERE id = %s RETURNING *""",
            (session_id,),
        ).fetchone()
    return _serialize(row)
