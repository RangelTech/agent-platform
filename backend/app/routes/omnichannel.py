"""Camada omnichannel: provisionamento e SSO para o Chatwoot.

O agent-platform continua sendo o cadastro principal. Estas rotas apenas
delegam para a ponte (`chatwoot-rt`), que fala com o Chatwoot — o backend
nunca guarda token de Platform API nem cria conta lá diretamente.
"""

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import require
from app.config import settings
from app.db import get_connection
from app.tenancy import resolve_target_tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/omnichannel", tags=["omnichannel"])

TIMEOUT = 60.0


class ProvisionIn(BaseModel):
    tenant_id: str | None = None  # master only


def _bridge_headers() -> dict:
    if not settings.bridge_url or not settings.bridge_admin_token:
        raise HTTPException(
            status_code=503, detail="Camada omnichannel não configurada nesta instalação"
        )
    return {"Authorization": f"Bearer {settings.bridge_admin_token}"}


async def _bridge(method: str, path: str, **kwargs):
    headers = _bridge_headers()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.request(
                method, f"{settings.bridge_url.rstrip('/')}{path}", headers=headers, **kwargs
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"ponte inacessível: {exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code, detail=f"ponte: {response.text[:300]}"
        )
    return response.json() if response.content else {}


@router.get("/status")
def status(user: dict = Depends(require("omnichannel", "view"))):
    """Se a empresa já tem conta no Chatwoot, e se a camada está ligada."""
    configured = bool(settings.bridge_url and settings.bridge_admin_token)
    account_id = None
    if user["tenant_id"]:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT chatwoot_account_id FROM tenants WHERE id = %s", (user["tenant_id"],)
            ).fetchone()
        account_id = row["chatwoot_account_id"] if row else None
    return {"configured": configured, "chatwoot_account_id": account_id}


@router.post("/provision")
async def provision(payload: ProvisionIn, user: dict = Depends(require("omnichannel", "edit"))):
    """Cria a conta do tenant e espelha o usuário atual. Idempotente."""
    tenant_id = resolve_target_tenant(user, payload.tenant_id)
    with get_connection() as conn:
        tenant = conn.execute("SELECT * FROM tenants WHERE id = %s", (tenant_id,)).fetchone()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    account = await _bridge(
        "POST",
        "/admin/tenants",
        json={
            "tenant_id": str(tenant["id"]),
            "tenant_key": tenant["tenant_key"],
            "tenant_name": tenant["name"],
        },
    )
    account_id = account.get("chatwoot_account_id")
    with get_connection() as conn:
        conn.execute(
            "UPDATE tenants SET chatwoot_account_id = %s WHERE id = %s", (account_id, tenant_id)
        )

    # O primeiro usuário provisionado vira administrador da conta: é ele quem
    # a ponte usa para criar inbox e operar a Application API.
    await _bridge(
        "POST",
        "/admin/users",
        json={
            "tenant_id": str(tenant["id"]),
            "platform_user_id": str(user["id"]),
            "email": user["email"],
            "name": user["name"],
            "role": "administrator",
        },
    )
    return {"status": "ok", "chatwoot_account_id": account_id}


@router.get("/sso")
async def sso(user: dict = Depends(require("omnichannel", "view"))):
    """Link temporário de login no Chatwoot — sem recadastro, sem senha nova."""
    if not user["tenant_id"]:
        raise HTTPException(status_code=400, detail="Usuário master não opera atendimento")
    body = await _bridge("GET", f"/admin/sso/{user['tenant_id']}/{user['id']}")
    return {"url": body.get("url")}
