"""Tenant datasources. The sensitive part (password / service-account JSON)
is encrypted at rest and write-only; connectivity tests run on the kernel,
where the database drivers live."""

import httpx
from fastapi import APIRouter, Depends, HTTPException
from psycopg.errors import UniqueViolation
from psycopg.types.json import Json
from pydantic import BaseModel, Field

from app.auth import require
from app.config import settings
from app.crypto import decrypt, encrypt
from app.db import get_connection
from app.tenancy import resolve_target_tenant

router = APIRouter(prefix="/api/datasources", tags=["datasources"])

KINDS = (
    "postgresql",
    "mysql",
    "bigquery",
    "sqlite",
    "sqlserver",
    "oracle",
    "firebird",
    "mongodb",
)


class DatasourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: str
    config: dict = Field(default_factory=dict)
    secret: str | None = Field(default=None, max_length=20_000)
    tenant_id: str | None = None  # master only


class DatasourceUpdate(BaseModel):
    name: str | None = None
    config: dict | None = None
    secret: str | None = Field(default=None, max_length=20_000)
    is_active: bool | None = None


def _serialize(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "name": row["name"],
        "kind": row["kind"],
        "config": row["config"],
        "has_secret": bool(row["secret_encrypted"]),
        "is_active": row["is_active"],
        "last_test_at": row["last_test_at"].isoformat() if row["last_test_at"] else None,
        "last_test_ok": row["last_test_ok"],
    }


def _scoped(conn, datasource_id: str, user: dict) -> dict:
    row = conn.execute(
        "SELECT * FROM datasources WHERE id = %s", (datasource_id,)
    ).fetchone()
    if row is None or (
        not user["is_master"] and str(row["tenant_id"]) != str(user["tenant_id"])
    ):
        raise HTTPException(status_code=404, detail="Fonte de dados não encontrada")
    return row


@router.get("")
def list_datasources(user: dict = Depends(require("datasources", "view"))):
    with get_connection() as conn:
        scope = (
            " WHERE NOT is_deleted"
            if user["is_master"]
            else " WHERE NOT is_deleted AND tenant_id = %s"
        )
        params = () if user["is_master"] else (user["tenant_id"],)
        rows = conn.execute(
            f"SELECT * FROM datasources{scope} ORDER BY name", params
        ).fetchall()
    return [_serialize(r) for r in rows]


@router.post("", status_code=201)
def create_datasource(
    payload: DatasourceIn, user: dict = Depends(require("datasources", "create"))
):
    if payload.kind not in KINDS:
        raise HTTPException(status_code=400, detail=f"Tipo inválido: {payload.kind}")
    tenant_id = resolve_target_tenant(user, payload.tenant_id)
    with get_connection() as conn:
        try:
            row = conn.execute(
                """INSERT INTO datasources (tenant_id, name, kind, config, secret_encrypted)
                   VALUES (%s, %s, %s, %s, %s) RETURNING *""",
                (
                    tenant_id,
                    payload.name,
                    payload.kind,
                    Json(payload.config),
                    encrypt(payload.secret) if payload.secret else None,
                ),
            ).fetchone()
        except UniqueViolation as exc:
            raise HTTPException(
                status_code=409, detail="Já existe uma fonte com esse nome"
            ) from exc
    return _serialize(row)


@router.put("/{datasource_id}")
def update_datasource(
    datasource_id: str,
    payload: DatasourceUpdate,
    user: dict = Depends(require("datasources", "edit")),
):
    fields, values = [], []
    if payload.name is not None:
        fields.append("name = %s")
        values.append(payload.name)
    if payload.config is not None:
        fields.append("config = %s")
        values.append(Json(payload.config))
    if payload.secret is not None:
        fields.append("secret_encrypted = %s")
        values.append(encrypt(payload.secret))
    if payload.is_active is not None:
        fields.append("is_active = %s")
        values.append(payload.is_active)
    if not fields:
        raise HTTPException(status_code=400, detail="Nada para atualizar")
    with get_connection() as conn:
        _scoped(conn, datasource_id, user)
        fields.append("updated_at = now()")
        values.append(datasource_id)
        row = conn.execute(
            f"UPDATE datasources SET {', '.join(fields)} WHERE id = %s RETURNING *",
            tuple(values),
        ).fetchone()
    return _serialize(row)


@router.post("/{datasource_id}/test")
async def test_datasource(
    datasource_id: str, user: dict = Depends(require("datasources", "edit"))
):
    with get_connection() as conn:
        row = _scoped(conn, datasource_id, user)

    spec = {
        "kind": row["kind"],
        "config": row["config"],
        "secret": decrypt(row["secret_encrypted"]) if row["secret_encrypted"] else None,
    }
    headers = {}
    if settings.kernel_audience:
        from app.gcp_auth import id_token_for

        headers["Authorization"] = f"Bearer {await id_token_for(settings.kernel_audience)}"
    elif settings.kernel_internal_token:
        headers["Authorization"] = f"Bearer {settings.kernel_internal_token}"

    ok, detail = False, ""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.kernel_url}/v1/test-datasource", json=spec, headers=headers
            )
            body = response.json()
            ok, detail = body.get("ok", False), body.get("detail", "")
    except httpx.HTTPError as exc:
        detail = f"kernel inacessível: {exc}"

    with get_connection() as conn:
        conn.execute(
            """UPDATE datasources
                  SET last_test_at = now(), last_test_ok = %s, updated_at = now()
                WHERE id = %s""",
            (ok, datasource_id),
        )
    return {"ok": ok, "detail": detail}


@router.delete("/{datasource_id}")
def archive_datasource(
    datasource_id: str, user: dict = Depends(require("datasources", "delete"))
):
    with get_connection() as conn:
        _scoped(conn, datasource_id, user)
        conn.execute(
            "UPDATE datasources SET is_deleted = TRUE, updated_at = now() WHERE id = %s",
            (datasource_id,),
        )
    return {"status": "ok"}
