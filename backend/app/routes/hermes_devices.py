"""Pareamento e gestão de dispositivos Hermes (seção 7.2 e 7.3 da spec de
integração). O pareamento usa o login já existente do RIA (email/senha) e
emite uma credencial própria do dispositivo — nunca a senha, nunca um token
OAuth de outro provider, nunca a sessão web do usuário."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import require, verify_credentials
from app.db import get_connection
from app.device_auth import issue_device_credential, require_device
from app.hermes_service import audit
from app.security import hash_token, new_session_token

router = APIRouter(prefix="/api/hermes/devices", tags=["hermes"])

# Ticket de handshake WSS (seção 7.2/8.1): curto o bastante para não valer a
# pena interceptar, longo o bastante para cobrir a reconexão da extensão
# (backoff) sem exigir uma segunda emissão a cada tentativa.
WS_TICKET_TTL_SECONDS = 30


class PairIn(BaseModel):
    email: str
    password: str
    device_name: str = Field(min_length=1, max_length=200)
    platform: str | None = None
    extension_version: str | None = None


class RenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


def _serialize(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "user_id": str(row["user_id"]),
        "name": row["name"],
        "platform": row["platform"],
        "extension_version": row["extension_version"],
        "status": row["status"],
        "last_seen_at": row["last_seen_at"].isoformat() if row["last_seen_at"] else None,
        "created_at": row["created_at"].isoformat(),
    }


def _scoped(conn, device_id: str, user: dict) -> dict:
    row = conn.execute("SELECT * FROM hermes_devices WHERE id = %s", (device_id,)).fetchone()
    if row is None or (
        not user["is_master"] and str(row["tenant_id"]) != str(user["tenant_id"])
    ):
        raise HTTPException(status_code=404, detail="Dispositivo não encontrado")
    return row


@router.post("/pair", status_code=201)
def pair_device(payload: PairIn):
    """Público (sem sessão prévia): é exatamente o que a extensão chama antes
    de ter qualquer credencial (seção 7.2). A senha nunca é persistida aqui."""
    user = verify_credentials(payload.email, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Email ou senha inválidos")
    if user["tenant_id"] is None:
        # O usuário master não representa um tenant operacional; pareamento
        # de dispositivo exige um tenant real dono da sessão Hermes.
        raise HTTPException(
            status_code=400, detail="A conta master não pode parear um dispositivo Hermes"
        )
    with get_connection() as conn:
        device = conn.execute(
            """INSERT INTO hermes_devices (tenant_id, user_id, name, platform, extension_version)
               VALUES (%s, %s, %s, %s, %s) RETURNING *""",
            (
                user["tenant_id"],
                user["id"],
                payload.device_name,
                payload.platform,
                payload.extension_version,
            ),
        ).fetchone()
        credential = issue_device_credential(conn, str(device["id"]))
        audit(
            conn,
            tenant_id=user["tenant_id"],
            actor_type="user",
            actor_id=str(user["id"]),
            action="hermes.device.paired",
            resource_type="hermes_device",
            resource_id=str(device["id"]),
            metadata={"name": payload.device_name, "platform": payload.platform},
        )
    return {"device": _serialize(device), "credential": credential}


@router.get("")
def list_devices(user: dict = Depends(require("hermes", "view"))):
    with get_connection() as conn:
        scope = "" if user["is_master"] else " WHERE tenant_id = %s"
        params = () if user["is_master"] else (user["tenant_id"],)
        rows = conn.execute(
            f"SELECT * FROM hermes_devices{scope} ORDER BY created_at DESC", params
        ).fetchall()
    return [_serialize(r) for r in rows]


@router.put("/{device_id}")
def rename_device(
    device_id: str, payload: RenameIn, user: dict = Depends(require("hermes", "edit"))
):
    with get_connection() as conn:
        _scoped(conn, device_id, user)
        row = conn.execute(
            "UPDATE hermes_devices SET name = %s, updated_at = now() WHERE id = %s RETURNING *",
            (payload.name, device_id),
        ).fetchone()
    return _serialize(row)


@router.post("/{device_id}/revoke")
def revoke_device(device_id: str, user: dict = Depends(require("hermes", "delete"))):
    """Encerra o acesso do dispositivo (critério de aceite 13): a credencial
    é revogada e nenhuma nova conexão é aceita. O histórico de sessões e
    chats não é apagado — isso exigiria uma ação explícita separada."""
    with get_connection() as conn:
        device = _scoped(conn, device_id, user)
        if device["status"] == "revoked":
            return {"status": "ok"}
        conn.execute(
            """UPDATE hermes_devices
                  SET status = 'revoked', revoked_at = now(), updated_at = now()
                WHERE id = %s""",
            (device_id,),
        )
        conn.execute(
            """UPDATE hermes_device_credentials SET revoked_at = now()
                WHERE device_id = %s AND revoked_at IS NULL""",
            (device_id,),
        )
        audit(
            conn,
            tenant_id=device["tenant_id"],
            actor_type="user",
            actor_id=str(user["id"]),
            action="hermes.device.revoked",
            resource_type="hermes_device",
            resource_id=device_id,
        )
    return {"status": "ok"}


@router.post("/ws-ticket")
def issue_ws_ticket(device: dict = Depends(require_device)):
    """Troca a credencial de longa duração por um ticket descartável para o
    handshake do Hermes Relay -- a credencial nunca vai na query string do
    WebSocket (ficaria em log de acesso do proxy). Uso único: o Relay marca
    `used_at` no mesmo UPDATE que valida, então uma segunda tentativa com o
    mesmo ticket (replay) sempre falha."""
    token = new_session_token()
    expires_at = datetime.now(UTC) + timedelta(seconds=WS_TICKET_TTL_SECONDS)
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO hermes_ws_tickets (tenant_id, device_id, token_hash, expires_at)
               VALUES (%s, %s, %s, %s)""",
            (device["tenant_id"], device["id"], hash_token(token), expires_at),
        )
    return {"ticket": token, "expires_at": expires_at.isoformat()}


@router.get("/me")
def whoami(device: dict = Depends(require_device)):
    """A extensão usa isto para confirmar a credencial e obter seu próprio
    device_id/tenant_id sem depender de nenhum estado local pré-existente."""
    return device
