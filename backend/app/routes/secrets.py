"""Named tenant secrets. Write-only values (encrypted at rest), referenced
from tool inputs as {{secret:NAME}} and resolved kernel-side per run."""

import re

from fastapi import APIRouter, Depends, HTTPException
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, Field

from app.auth import require
from app.crypto import encrypt
from app.db import get_connection
from app.tenancy import resolve_target_tenant

router = APIRouter(prefix="/api/secrets", tags=["secrets"])

_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


class SecretIn(BaseModel):
    name: str
    value: str = Field(min_length=1, max_length=10_000)
    tenant_id: str | None = None  # master only


class SecretUpdate(BaseModel):
    value: str | None = Field(default=None, min_length=1, max_length=10_000)
    is_active: bool | None = None


def _serialize(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "name": row["name"],
        "is_active": row["is_active"],
        "created_at": row["created_at"].isoformat(),
    }


@router.get("")
def list_secrets(user: dict = Depends(require("secrets", "view"))):
    with get_connection() as conn:
        scope = " WHERE NOT is_deleted" if user["is_master"] else " WHERE NOT is_deleted AND tenant_id = %s"
        params = () if user["is_master"] else (user["tenant_id"],)
        rows = conn.execute(f"SELECT * FROM secrets{scope} ORDER BY name", params).fetchall()
    return [_serialize(r) for r in rows]


@router.post("", status_code=201)
def create_secret(payload: SecretIn, user: dict = Depends(require("secrets", "create"))):
    if not _NAME.match(payload.name):
        raise HTTPException(status_code=400, detail="Nome inválido (A-Z, 0-9, _ . -)")
    tenant_id = resolve_target_tenant(user, payload.tenant_id)
    with get_connection() as conn:
        try:
            row = conn.execute(
                """INSERT INTO secrets (tenant_id, name, value_encrypted)
                   VALUES (%s, %s, %s) RETURNING *""",
                (tenant_id, payload.name, encrypt(payload.value)),
            ).fetchone()
        except UniqueViolation as exc:
            raise HTTPException(
                status_code=409, detail="Já existe um segredo com esse nome"
            ) from exc
    return _serialize(row)


@router.put("/{secret_id}")
def update_secret(
    secret_id: str, payload: SecretUpdate, user: dict = Depends(require("secrets", "edit"))
):
    fields, values = [], []
    if payload.value is not None:
        fields.append("value_encrypted = %s")
        values.append(encrypt(payload.value))
    if payload.is_active is not None:
        fields.append("is_active = %s")
        values.append(payload.is_active)
    if not fields:
        raise HTTPException(status_code=400, detail="Nada para atualizar")
    fields.append("updated_at = now()")
    values.append(secret_id)
    scope = "" if user["is_master"] else " AND tenant_id = %s"
    if not user["is_master"]:
        values.append(user["tenant_id"])
    with get_connection() as conn:
        row = conn.execute(
            f"UPDATE secrets SET {', '.join(fields)} WHERE id = %s{scope} RETURNING *",
            tuple(values),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Segredo não encontrado")
    return _serialize(row)


@router.delete("/{secret_id}")
def archive_secret(secret_id: str, user: dict = Depends(require("secrets", "delete"))):
    scope = "" if user["is_master"] else " AND tenant_id = %s"
    params = [secret_id] + ([] if user["is_master"] else [user["tenant_id"]])
    with get_connection() as conn:
        row = conn.execute(
            f"UPDATE secrets SET is_deleted = TRUE, updated_at = now()"
            f" WHERE id = %s{scope} RETURNING id",
            tuple(params),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Segredo não encontrado")
    return {"status": "ok"}
