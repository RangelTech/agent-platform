"""Rotas internas chamadas pelo Rails do Chatwoot (spec 10): ciclo de vida da empresa e /me.

Autenticação: contexto assinado (`app.ui_context`). A empresa (tenant) é a
`Account` do Chatwoot; o motor só reage aos eventos dela. Tudo idempotente.
"""

import logging
import re
import unicodedata

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from psycopg.types.json import Json
from pydantic import BaseModel, Field

from app import runtime_client as rt
from app.db import get_connection
from app.permissions import ADMIN_PERMISSIONS, MEMBER_PERMISSIONS
from app.routes.tenants import _provisionar_router_em_background
from app.ui_context import install_actor, service_actor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/ui", tags=["ui-internal"])


class ProvisionIn(BaseModel):
    account_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)


class AccountRef(BaseModel):
    account_id: int = Field(ge=1)


def _need(actor: dict, scope: str) -> None:
    if scope not in actor["scopes"]:
        raise HTTPException(status_code=403, detail="Permissão negada")


def _slug(name: str, account_id: int) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:40] or "empresa"
    return f"{base}-{account_id}"


def _tenant_by_account(conn, account_id: int):
    return conn.execute(
        "SELECT * FROM tenants WHERE chatwoot_account_id = %s", (account_id,)
    ).fetchone()


def _rm(method: str, path: str, tenant_id, body: dict | None = None) -> None:
    """Chama o gerenciador de ambientes; indisponível/inexistente não derruba o ciclo de vida."""
    if not rt.configured():
        return
    rmt = rt.rm_tenant(tenant_id)
    try:
        rt.call(method, f"/v1/tenants/{rmt}{path}", scope="*", body=body, timeout=60.0)
    except rt.RMError as exc:
        if exc.status != 404:
            raise HTTPException(
                status_code=502, detail="O ambiente não aceitou a operação"
            ) from exc


@router.post("/provision")
def provision(
    payload: ProvisionIn, background: BackgroundTasks, actor: dict = Depends(install_actor)
):
    _need(actor, "lifecycle")
    from app.ragentes_guide import ensure_for_tenant

    created = False
    with get_connection() as conn:
        tenant = _tenant_by_account(conn, payload.account_id)
        if tenant is None:
            tenant = conn.execute(
                """INSERT INTO tenants (tenant_key, name, chatwoot_account_id, provisioned_at)
                   VALUES (%s, %s, %s, now()) RETURNING *""",
                (_slug(payload.name, payload.account_id), payload.name, payload.account_id),
            ).fetchone()
            created = True
            for name, permissions in (
                ("Administrador", ADMIN_PERMISSIONS),
                ("Usuário", MEMBER_PERMISSIONS),
            ):
                conn.execute(
                    "INSERT INTO user_profiles (tenant_id, name, permissions) VALUES (%s, %s, %s)",
                    (tenant["id"], name, Json(permissions)),
                )
        else:
            conn.execute(
                "UPDATE tenants SET provisioned_at = COALESCE(provisioned_at, now()) WHERE id = %s",
                (tenant["id"],),
            )
        ensure_for_tenant(conn, tenant["id"])
    if created:
        background.add_task(
            _provisionar_router_em_background, str(tenant["id"]), tenant["tenant_key"]
        )
    return {"status": "ok", "engine_tenant_id": str(tenant["id"]), "created": created}


def _set_active(account_id: int, active: bool, by_chatwoot: bool):
    with get_connection() as conn:
        tenant = _tenant_by_account(conn, account_id)
        if tenant is None:
            raise HTTPException(status_code=404, detail="Empresa não provisionada")
        conn.execute(
            "UPDATE tenants SET is_active = %s, suspended_by_chatwoot = %s, updated_at = now() WHERE id = %s",
            (active, by_chatwoot, tenant["id"]),
        )
    return tenant


@router.post("/suspend")
def suspend(payload: AccountRef, actor: dict = Depends(install_actor)):
    _need(actor, "lifecycle")
    tenant = _set_active(payload.account_id, False, True)
    _rm(
        "POST",
        ":suspend",
        tenant["id"],
        {"reason": "Account suspensa no Chatwoot", "severity": "high"},
    )
    return {"status": "ok", "engine_tenant_id": str(tenant["id"])}


@router.post("/resume")
def resume(payload: AccountRef, actor: dict = Depends(install_actor)):
    _need(actor, "lifecycle")
    tenant = _set_active(payload.account_id, True, False)
    _rm("POST", ":resume", tenant["id"], {"approver": "chatwoot-super-admin"})
    return {"status": "ok", "engine_tenant_id": str(tenant["id"])}


@router.post("/deprovision")
def deprovision(payload: AccountRef, actor: dict = Depends(install_actor)):
    """Desliga a empresa. **Nunca apaga dados** (decisão do dono): fica inativa e preservada."""
    _need(actor, "lifecycle")
    tenant = _set_active(payload.account_id, False, True)
    _rm(
        "POST",
        ":suspend",
        tenant["id"],
        {"reason": "Account excluída no Chatwoot", "severity": "high"},
    )
    return {"status": "ok", "engine_tenant_id": str(tenant["id"]), "data_retained": True}


@router.get("/me")
def me(actor: dict = Depends(service_actor)):
    return {
        "tenant_id": actor["tenant_id"],
        "user_id": actor["user_id"],
        "chatwoot_account_id": actor["chatwoot_account_id"],
        "scopes": sorted(actor["scopes"]),
        "flags": actor["flags"],
    }
