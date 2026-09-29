"""Comandos remotos (seção 6.4/8.3 da spec de integração): o usuário envia,
o dispositivo consome e reporta. Sem Relay ainda (Fase B) a entrega em tempo
real fica para depois; esta camada já é o contrato completo e idempotente —
o Relay que chegar depois só troca o transporte, não o modelo."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.types.json import Json
from pydantic import BaseModel, Field

from app.auth import require
from app.db import get_connection
from app.device_auth import require_device
from app.hermes_service import COMMAND_TERMINAL, COMMAND_TRANSITIONS, audit

router = APIRouter(prefix="/api/hermes", tags=["hermes"])


class CommandIn(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=200)
    payload: dict = Field(default_factory=dict)
    deadline_seconds: int | None = Field(default=None, ge=1, le=24 * 3600)


class TransitionIn(BaseModel):
    status: str
    result: dict | None = None
    error: str | None = Field(default=None, max_length=2000)


def _serialize(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "session_id": str(row["session_id"]),
        "device_id": str(row["device_id"]),
        "issued_by": str(row["issued_by"]),
        "idempotency_key": row["idempotency_key"],
        "payload": row["payload"],
        "status": row["status"],
        "result": row["result"],
        "error": row["error"],
        "deadline_at": row["deadline_at"].isoformat() if row["deadline_at"] else None,
        "delivered_at": row["delivered_at"].isoformat() if row["delivered_at"] else None,
        "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


def _session_for_user(conn, session_id: str, user: dict) -> dict:
    row = conn.execute(
        "SELECT * FROM hermes_sessions WHERE id = %s", (session_id,)
    ).fetchone()
    if row is None or (
        not user["is_master"] and str(row["tenant_id"]) != str(user["tenant_id"])
    ):
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    return row


def _command_for_device(conn, command_id: str, device: dict) -> dict:
    row = conn.execute(
        "SELECT * FROM hermes_commands WHERE id = %s", (command_id,)
    ).fetchone()
    if row is None or str(row["device_id"]) != device["id"]:
        raise HTTPException(status_code=404, detail="Comando não encontrado")
    return row


def _command_for_user(conn, command_id: str, user: dict) -> dict:
    row = conn.execute(
        "SELECT * FROM hermes_commands WHERE id = %s", (command_id,)
    ).fetchone()
    if row is None or (
        not user["is_master"] and str(row["tenant_id"]) != str(user["tenant_id"])
    ):
        raise HTTPException(status_code=404, detail="Comando não encontrado")
    return row


# ---------------------------------------------------------------- usuário


@router.post("/sessions/{session_id}/commands", status_code=201)
def create_command(
    session_id: str, payload: CommandIn, user: dict = Depends(require("hermes", "create"))
):
    with get_connection() as conn:
        session = _session_for_user(conn, session_id, user)
        if session["status"] == "ended":
            raise HTTPException(
                status_code=409, detail="Esta sessão foi encerrada e não aceita novos comandos"
            )
        existing = conn.execute(
            "SELECT * FROM hermes_commands WHERE session_id = %s AND idempotency_key = %s",
            (session_id, payload.idempotency_key),
        ).fetchone()
        if existing is not None:
            # Reenvio (retry de rede) devolve o comando já existente — nunca
            # cria um segundo alvo para a mesma instrução (fluxo obrigatório,
            # seção 6.4).
            return _serialize(existing)

        deadline = (
            datetime.now(UTC) + timedelta(seconds=payload.deadline_seconds)
            if payload.deadline_seconds
            else None
        )
        row = conn.execute(
            """INSERT INTO hermes_commands
                   (tenant_id, session_id, device_id, issued_by, idempotency_key,
                    payload, deadline_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (
                session["tenant_id"],
                session_id,
                session["device_id"],
                user["id"],
                payload.idempotency_key,
                Json(payload.payload),
                deadline,
            ),
        ).fetchone()
        audit(
            conn,
            tenant_id=session["tenant_id"],
            actor_type="user",
            actor_id=str(user["id"]),
            action="hermes.command.created",
            resource_type="hermes_command",
            resource_id=str(row["id"]),
            metadata={"session_id": session_id},
        )
        # NOTIFY so a connected Hermes Relay can push the device an
        # immediate "you have work" ping instead of it waiting for its
        # next poll -- Postgres only delivers this once the transaction
        # actually commits, so a rollback above never fires a false one.
        conn.execute("SELECT pg_notify('hermes_commands', %s)", (str(session["device_id"]),))
    return _serialize(row)


@router.get("/sessions/{session_id}/commands")
def list_session_commands(
    session_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(require("hermes", "view")),
):
    with get_connection() as conn:
        _session_for_user(conn, session_id, user)
        rows = conn.execute(
            """SELECT * FROM hermes_commands WHERE session_id = %s
                ORDER BY created_at DESC LIMIT %s OFFSET %s""",
            (session_id, limit, offset),
        ).fetchall()
    return [_serialize(r) for r in rows]


@router.post("/commands/{command_id}/cancel")
def cancel_command(command_id: str, user: dict = Depends(require("hermes", "edit"))):
    with get_connection() as conn:
        command = _command_for_user(conn, command_id, user)
        if command["status"] in COMMAND_TERMINAL:
            raise HTTPException(
                status_code=409, detail=f"Comando já está em estado final: {command['status']}"
            )
        row = conn.execute(
            """UPDATE hermes_commands SET status = 'cancelled', updated_at = now()
                WHERE id = %s RETURNING *""",
            (command_id,),
        ).fetchone()
        audit(
            conn,
            tenant_id=command["tenant_id"],
            actor_type="user",
            actor_id=str(user["id"]),
            action="hermes.command.cancelled",
            resource_type="hermes_command",
            resource_id=command_id,
        )
    return _serialize(row)


# ---------------------------------------------------------------- dispositivo


@router.get("/commands/pending")
def pending_commands(device: dict = Depends(require_device)):
    """Reivindicação atômica: o comando muda de `queued` para `delivered`
    nesta mesma consulta, então duas chamadas concorrentes do mesmo
    dispositivo nunca recebem o mesmo comando duas vezes."""
    with get_connection() as conn:
        rows = conn.execute(
            """UPDATE hermes_commands
                  SET status = 'delivered', delivered_at = now(), updated_at = now()
                WHERE id IN (
                    SELECT id FROM hermes_commands
                     WHERE device_id = %s AND status = 'queued'
                     ORDER BY created_at ASC
                     FOR UPDATE SKIP LOCKED
                )
                RETURNING *""",
            (device["id"],),
        ).fetchall()
    return [_serialize(r) for r in rows]


@router.post("/commands/{command_id}/transition")
def transition_command(
    command_id: str, payload: TransitionIn, device: dict = Depends(require_device)
):
    with get_connection() as conn:
        command = _command_for_device(conn, command_id, device)
        allowed = COMMAND_TRANSITIONS.get(command["status"], set())
        if payload.status not in allowed:
            raise HTTPException(
                status_code=409,
                detail=f"Transição inválida: {command['status']} -> {payload.status}",
            )
        completed_at = "now()" if payload.status in COMMAND_TERMINAL else "completed_at"
        row = conn.execute(
            f"""UPDATE hermes_commands
                   SET status = %s, result = %s, error = %s,
                       updated_at = now(), completed_at = {completed_at}
                 WHERE id = %s RETURNING *""",
            (
                payload.status,
                Json(payload.result) if payload.result is not None else None,
                payload.error,
                command_id,
            ),
        ).fetchone()
        audit(
            conn,
            tenant_id=command["tenant_id"],
            actor_type="device",
            actor_id=device["id"],
            action=f"hermes.command.{payload.status}",
            resource_type="hermes_command",
            resource_id=command_id,
        )
    return _serialize(row)
