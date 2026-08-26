"""Credenciais capturadas pelo RAtende Connector (produto-15) -- providers
"não oficiais" (Instagram/Facebook/TikTok), sessão de navegador em vez de
OAuth. Mesmo padrão de tenant-scoping/criptografia de `google_accounts.py`,
mas sem motor de OAuth nenhum: o payload já chega pronto (cookies
capturados pela extensão), o backend só cifra e guarda.

Escopo desta fase (produto-15 §6c): só captura e armazenamento. Nenhum
consumo real (ler/mandar mensagem) acontece aqui -- fica pra uma spec de
integração com o `chatwoot-rt` depois que a captura for provada estável.
"""

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import require
from app.crypto import encrypt
from app.db import get_connection
from app.tenancy import resolve_target_tenant

router = APIRouter(prefix="/api/unofficial-connections", tags=["unofficial-connections"])

_PROVIDERS_VALIDOS = {"instagram_web", "facebook_web", "tiktok_web"}


class CookieIn(BaseModel):
    name: str
    value: str
    domain: str
    path: str = "/"


class UnofficialConnectionIn(BaseModel):
    provider: str
    label: str = Field(min_length=1, max_length=120)
    external_label: str | None = Field(default=None, max_length=120)
    cookies: list[CookieIn] = Field(min_length=1)
    tenant_id: str | None = None  # master only


def _serialize(row: dict) -> dict:
    # cookies_encrypted NUNCA volta -- mesma regra de senha/token em
    # email_accounts.py/google_accounts.py.
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "provider": row["provider"],
        "label": row["label"],
        "external_label": row["external_label"],
        "status": row["status"],
        "updated_at": row["updated_at"].isoformat(),
    }


@router.get("")
def list_unofficial_connections(
    user: dict = Depends(require("unofficial_connections", "view")),
):
    tenant_id = resolve_target_tenant(user, None)
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM tenant_unofficial_connections
                WHERE tenant_id = %s AND is_active ORDER BY created_at""",
            (tenant_id,),
        ).fetchall()
    return [_serialize(r) for r in rows]


@router.post("", status_code=201)
def create_unofficial_connection(
    payload: UnofficialConnectionIn,
    user: dict = Depends(require("unofficial_connections", "create")),
):
    if payload.provider not in _PROVIDERS_VALIDOS:
        validos = sorted(_PROVIDERS_VALIDOS)
        raise HTTPException(
            status_code=400,
            detail=f"provider inválido: {payload.provider!r} (esperado um de {validos})",
        )
    tenant_id = resolve_target_tenant(user, payload.tenant_id)
    cookies_json = json.dumps([c.model_dump() for c in payload.cookies])
    with get_connection() as conn:
        row = conn.execute(
            """INSERT INTO tenant_unofficial_connections
                   (tenant_id, provider, label, external_label, cookies_encrypted)
               VALUES (%s, %s, %s, %s, %s)
               RETURNING *""",
            (
                tenant_id,
                payload.provider,
                payload.label,
                payload.external_label,
                encrypt(cookies_json),
            ),
        ).fetchone()
    return _serialize(row)


@router.delete("/{connection_id}", status_code=204)
def delete_unofficial_connection(
    connection_id: str, user: dict = Depends(require("unofficial_connections", "delete"))
):
    tenant_id = resolve_target_tenant(user, None)
    with get_connection() as conn:
        row = conn.execute(
            """UPDATE tenant_unofficial_connections SET is_active = false
                WHERE id = %s AND tenant_id = %s RETURNING id""",
            (connection_id, tenant_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Conexão não encontrada")
