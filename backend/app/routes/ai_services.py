"""AI service (BYOK) management. The API key is write-only: stored encrypted,
never echoed back. Connectivity tests go through the kernel so provider SDKs
live in one place only."""

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

router = APIRouter(prefix="/api/ai-services", tags=["ai_services"])

PROVIDERS = ("gemini", "openai", "anthropic", "deepseek", "groq", "openai-compatible")


class AiServiceIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    provider: str
    model: str = Field(min_length=1, max_length=200)
    api_key: str = Field(min_length=1, max_length=500)
    api_base: str | None = None
    tenant_id: str | None = None  # master only


class AiServiceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    model: str | None = None
    api_key: str | None = Field(default=None, min_length=1, max_length=500)
    api_base: str | None = None
    is_active: bool | None = None


def _serialize(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "name": row["name"],
        "provider": row["provider"],
        "model": row["model"],
        "auth_type": row["auth_type"],
        "api_base": row["api_base"],
        "is_active": row["is_active"],
        "has_key": bool(row["api_key_encrypted"]),
        "last_test_at": row["last_test_at"].isoformat() if row["last_test_at"] else None,
        "last_test_ok": row["last_test_ok"],
    }


@router.get("")
def list_services(user: dict = Depends(require("ai_services", "view"))):
    with get_connection() as conn:
        if user["is_master"]:
            rows = conn.execute(
                "SELECT * FROM ai_services WHERE NOT is_deleted ORDER BY name"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ai_services WHERE NOT is_deleted AND tenant_id = %s ORDER BY name",
                (user["tenant_id"],),
            ).fetchall()
    return [_serialize(r) for r in rows]


@router.post("", status_code=201)
def create_service(
    payload: AiServiceIn, user: dict = Depends(require("ai_services", "create"))
):
    if payload.provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Provider inválido: {payload.provider}")
    if payload.provider == "openai-compatible" and not payload.api_base:
        raise HTTPException(
            status_code=400, detail="Provider openai-compatible exige api_base"
        )
    tenant_id = resolve_target_tenant(user, payload.tenant_id)
    with get_connection() as conn:
        try:
            row = conn.execute(
                """INSERT INTO ai_services
                       (tenant_id, name, provider, model, api_key_encrypted, api_base, config)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *""",
                (
                    tenant_id,
                    payload.name,
                    payload.provider,
                    payload.model,
                    encrypt(payload.api_key),
                    payload.api_base,
                    Json({}),
                ),
            ).fetchone()
        except UniqueViolation as exc:
            raise HTTPException(
                status_code=409, detail="Já existe um serviço com esse nome"
            ) from exc
    return _serialize(row)


def _get_scoped(conn, service_id: str, user: dict) -> dict:
    row = conn.execute(
        "SELECT * FROM ai_services WHERE id = %s", (service_id,)
    ).fetchone()
    if row is None or (
        not user["is_master"] and str(row["tenant_id"]) != str(user["tenant_id"])
    ):
        raise HTTPException(status_code=404, detail="Serviço não encontrado")
    return row


@router.put("/{service_id}")
def update_service(
    service_id: str,
    payload: AiServiceUpdate,
    user: dict = Depends(require("ai_services", "edit")),
):
    fields, values = [], []
    if payload.name is not None:
        fields.append("name = %s")
        values.append(payload.name)
    if payload.model is not None:
        fields.append("model = %s")
        values.append(payload.model)
    if payload.api_key is not None:
        fields.append("api_key_encrypted = %s")
        values.append(encrypt(payload.api_key))
    if payload.api_base is not None:
        fields.append("api_base = %s")
        values.append(payload.api_base)
    if payload.is_active is not None:
        fields.append("is_active = %s")
        values.append(payload.is_active)
    if not fields:
        raise HTTPException(status_code=400, detail="Nada para atualizar")

    with get_connection() as conn:
        _get_scoped(conn, service_id, user)
        fields.append("updated_at = now()")
        values.append(service_id)
        row = conn.execute(
            f"UPDATE ai_services SET {', '.join(fields)} WHERE id = %s RETURNING *",
            tuple(values),
        ).fetchone()
    return _serialize(row)


@router.post("/{service_id}/test")
async def test_service(
    service_id: str, user: dict = Depends(require("ai_services", "edit"))
):
    with get_connection() as conn:
        row = _get_scoped(conn, service_id, user)

    spec = {
        "provider": "openai" if row["provider"] == "openai-compatible" else row["provider"],
        "model": row["model"],
        "api_key": decrypt(row["api_key_encrypted"]),
        "api_base": row["api_base"],
    }
    headers = {}
    if settings.kernel_audience:
        from app.gcp_auth import id_token_for

        headers["Authorization"] = f"Bearer {await id_token_for(settings.kernel_audience)}"
    elif settings.kernel_internal_token:
        headers["Authorization"] = f"Bearer {settings.kernel_internal_token}"

    ok, detail = False, ""
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                f"{settings.kernel_url}/v1/test-model", json=spec, headers=headers
            )
            body = response.json()
            ok = body.get("ok", False)
            detail = body.get("detail", "")
    except httpx.HTTPError as exc:
        detail = f"kernel inacessível: {exc}"

    with get_connection() as conn:
        conn.execute(
            """UPDATE ai_services
                  SET last_test_at = now(), last_test_ok = %s, updated_at = now()
                WHERE id = %s""",
            (ok, service_id),
        )
    return {"ok": ok, "detail": detail}


@router.delete("/{service_id}")
def archive_service(service_id: str, user: dict = Depends(require("ai_services", "delete"))):
    with get_connection() as conn:
        _get_scoped(conn, service_id, user)
        conn.execute(
            "UPDATE ai_services SET is_deleted = TRUE, updated_at = now() WHERE id = %s",
            (service_id,),
        )
    return {"status": "ok"}
