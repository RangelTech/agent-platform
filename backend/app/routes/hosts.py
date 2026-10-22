"""Computadores conectados (spec 11 §26): lista com online/offline, pareamento, remoção e
concessão ao agente. A chave privada da conexão nunca sai daqui."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, Field

from app import hosts as hub
from app.auth import current_user, require
from app.crypto import encrypt
from app.db import get_connection
from app.permissions import has_permission

router = APIRouter(prefix="/api/hosts", tags=["hosts"])


def _can_manage(user: dict) -> bool:
    if user["tenant_id"] is None:
        return False
    if user.get("via") == "chatwoot":
        return "ai.environment.manage" in set(user.get("scopes", ()))
    return has_permission(user, "environment", "manage")


def _require_manage(user: dict) -> None:
    if not _can_manage(user):
        raise HTTPException(status_code=403, detail="Permissão negada")


def _view(row: dict, st: dict | None) -> dict:
    st = st or {}
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "os": st.get("os") or row["os"],
        "arch": st.get("arch") or row["arch"],
        "version": st.get("version"),
        "paired": bool(st.get("paired")),
        "online": bool(st.get("online")),
        "last_seen": st.get("last_seen"),
        "created_at": row["created_at"].isoformat(),
    }


class HostIn(BaseModel):
    name: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9][A-Za-z0-9 _.-]*$")


@router.get("")
def list_hosts(user: dict = Depends(require("environment", "view"))):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM hosts WHERE tenant_id = %s AND NOT is_deleted ORDER BY name",
            (user["tenant_id"],),
        ).fetchall()
    status = hub.hub_status(str(user["tenant_id"]))
    return {"hosts": [_view(r, status.get(str(r["id"]))) for r in rows]}


@router.post("", status_code=201)
def create_host(payload: HostIn, user: dict = Depends(current_user)):
    _require_manage(user)
    priv, pub = hub.new_keypair()
    with get_connection() as conn:
        try:
            row = conn.execute(
                """INSERT INTO hosts (tenant_id, name, conn_pub, conn_key_encrypted, created_by)
                   VALUES (%s, %s, %s, %s, %s) RETURNING *""",
                (user["tenant_id"], payload.name.strip(), pub, encrypt(priv), user["id"]),
            ).fetchone()
        except UniqueViolation:
            raise HTTPException(
                status_code=409, detail="Já existe um computador com esse nome"
            ) from None
    return _pair(row, user)


def _pair(row: dict, user: dict) -> dict:
    try:
        info = hub.hub_pair(str(row["id"]), str(user["tenant_id"]), row["name"], row["conn_pub"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="Serviço de conexão indisponível") from exc
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "code": info["code"],
        "expires_in": info["expires_in"],
    }


@router.post("/{host_id}/pair")
def new_code(host_id: str, user: dict = Depends(current_user)):
    """Novo código de pareamento (reinstalar o conector); o computador antigo deixa de valer."""
    _require_manage(user)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM hosts WHERE id::text = %s AND tenant_id = %s AND NOT is_deleted",
            (host_id, user["tenant_id"]),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Computador não encontrado")
    return _pair(row, user)


@router.put("/{host_id}")
def rename_host(host_id: str, payload: HostIn, user: dict = Depends(current_user)):
    _require_manage(user)
    with get_connection() as conn:
        try:
            row = conn.execute(
                """UPDATE hosts SET name = %s WHERE id::text = %s AND tenant_id = %s AND NOT is_deleted
                   RETURNING *""",
                (payload.name.strip(), host_id, user["tenant_id"]),
            ).fetchone()
        except UniqueViolation:
            raise HTTPException(
                status_code=409, detail="Já existe um computador com esse nome"
            ) from None
    if row is None:
        raise HTTPException(status_code=404, detail="Computador não encontrado")
    return _view(row, hub.hub_status(str(user["tenant_id"])).get(host_id))


@router.delete("/{host_id}")
def remove_host(host_id: str, user: dict = Depends(current_user)):
    _require_manage(user)
    with get_connection() as conn:
        row = conn.execute(
            """UPDATE hosts SET is_deleted = TRUE, name = name || '~del~' || left(id::text, 8)
                WHERE id::text = %s AND tenant_id = %s AND NOT is_deleted RETURNING id""",
            (host_id, user["tenant_id"]),
        ).fetchone()
        if row:
            conn.execute("DELETE FROM agent_hosts WHERE host_id = %s", (row["id"],))
    if row is None:
        raise HTTPException(status_code=404, detail="Computador não encontrado")
    hub.hub_revoke(host_id)
    return {"ok": True}


@router.post("/{host_id}/test")
def test_host(host_id: str, user: dict = Depends(require("environment", "view"))):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM hosts WHERE id::text = %s AND tenant_id = %s AND NOT is_deleted",
            (host_id, user["tenant_id"]),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Computador não encontrado")
    st = hub.hub_status(str(user["tenant_id"])).get(host_id) or {}
    if not st.get("paired"):
        return {
            "ok": False,
            "detail": "Ainda não foi pareado. Instale o conector e informe o código.",
        }
    if not st.get("online"):
        return {
            "ok": False,
            "detail": "Computador offline. Ligue o computador e confira a internet.",
        }
    return {"ok": True, "detail": "Computador online."}


class AgentHostsIn(BaseModel):
    host_ids: list[str] = Field(default_factory=list, max_length=100)


@router.get("/agent/{template_id}")
def get_agent_hosts(template_id: str, user: dict = Depends(require("environment", "view"))):
    with get_connection() as conn:
        linked = {
            str(r["host_id"])
            for r in conn.execute(
                "SELECT host_id FROM agent_hosts WHERE template_id::text = %s AND tenant_id = %s",
                (template_id, user["tenant_id"]),
            ).fetchall()
        }
        rows = conn.execute(
            "SELECT id, name FROM hosts WHERE tenant_id = %s AND NOT is_deleted ORDER BY name",
            (user["tenant_id"],),
        ).fetchall()
    return {
        "hosts": [
            {"id": str(r["id"]), "name": r["name"], "linked": str(r["id"]) in linked} for r in rows
        ]
    }


@router.put("/agent/{template_id}")
def set_agent_hosts(template_id: str, payload: AgentHostsIn, user: dict = Depends(current_user)):
    _require_manage(user)
    with get_connection() as conn:
        tpl = conn.execute(
            "SELECT id FROM templates WHERE id::text = %s AND tenant_id = %s",
            (template_id, user["tenant_id"]),
        ).fetchone()
        if tpl is None:
            raise HTTPException(status_code=404, detail="Agente não encontrado")
        valid = {
            str(r["id"])
            for r in conn.execute(
                "SELECT id FROM hosts WHERE tenant_id = %s AND NOT is_deleted", (user["tenant_id"],)
            ).fetchall()
        }
        ids = [i for i in dict.fromkeys(payload.host_ids) if i in valid]
        conn.execute("DELETE FROM agent_hosts WHERE template_id = %s", (tpl["id"],))
        for hid in ids:
            conn.execute(
                "INSERT INTO agent_hosts (template_id, host_id, tenant_id) VALUES (%s, %s, %s)",
                (tpl["id"], hid, user["tenant_id"]),
            )
    return {"linked": ids}
