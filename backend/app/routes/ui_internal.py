"""Rotas internas chamadas pelo Rails do Chatwoot (spec 10): ciclo de vida da empresa e /me.

Autenticação: contexto assinado (`app.ui_context`). A empresa (tenant) é a
`Account` do Chatwoot; o motor só reage aos eventos dela. Tudo idempotente.
"""

import asyncio
import logging
import re
import unicodedata

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from psycopg.types.json import Json
from pydantic import BaseModel, Field

from app import gateway_client
from app import runtime_client as rt
from app.db import get_connection
from app.installation_secrets import resolver as resolver_segredo
from app.permissions import ADMIN_PERMISSIONS, MEMBER_PERMISSIONS
from app.router_provisioning import _provisionar_router_em_background
from app.ui_context import install_actor, service_actor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/ui", tags=["ui-internal"])


class ProvisionIn(BaseModel):
    account_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)


class AccountRef(BaseModel):
    account_id: int = Field(ge=1)


class MemberRevokedIn(BaseModel):
    account_id: int = Field(ge=1)
    chatwoot_user_id: int = Field(ge=1)
    # Hora da remoção no Rails (epoch s). Contexto emitido antes dela é recusado; depois, reativa.
    revoked_at: float | None = None


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


def _sync_gateway_status(conn, tenant: dict, active: bool) -> None:
    """Espelha suspensão/retomada no gateway sem tocar tenants legados.

    A remoção física do gateway é deliberadamente excluída daqui: offboarding
    em cascata exige ordem explícita do dono e continua com dados retidos.
    """
    row = conn.execute(
        """SELECT gateway_tenant_id FROM tenant_routers
             WHERE tenant_id = %s AND is_active""",
        (tenant["id"],),
    ).fetchone()
    if not row or not row.get("gateway_tenant_id"):
        return
    try:
        asyncio.run(
            gateway_client.set_tenant_status(
                resolver_segredo("GATEWAY_BASE_URL"),
                resolver_segredo("GATEWAY_ADMIN_TOKEN"),
                tenant_id=row["gateway_tenant_id"],
                active=active,
            )
        )
    except gateway_client.GatewayError as exc:
        raise HTTPException(
            status_code=502, detail="O Gateway de IA não aceitou a operação"
        ) from exc


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
        _sync_gateway_status(conn, tenant, active)
        conn.execute(
            "UPDATE tenants SET is_active = %s, suspended_by_chatwoot = %s, "
            "updated_at = now() WHERE id = %s",
            (active, by_chatwoot, tenant["id"]),
        )
    return tenant


@router.post("/suspend")
async def suspend(payload: AccountRef, actor: dict = Depends(install_actor)):
    _need(actor, "lifecycle")
    tenant = await asyncio.to_thread(_set_active, payload.account_id, False, True)
    from app.routes.chats import cancel_tenant_runs

    await cancel_tenant_runs(tenant["id"])
    _rm(
        "POST",
        ":suspend",
        tenant["id"],
        {"reason": "Account suspensa no Chatwoot", "severity": "high"},
    )
    return {"status": "ok", "engine_tenant_id": str(tenant["id"])}


@router.post("/member-revoked")
async def member_revoked(payload: MemberRevokedIn, actor: dict = Depends(install_actor)):
    """Usuário removido da empresa: projeção inativa, runs canceladas, novas chamadas = 403.

    O stream aberto encerra com "Seu acesso foi removido" pelo vigia do repasse (poucos
    segundos). Idempotente: repetir o evento não muda nada além de reafirmar o estado.
    """
    _need(actor, "lifecycle")
    with get_connection() as conn:
        tenant = _tenant_by_account(conn, payload.account_id)
        if tenant is None:
            return {"status": "ok", "revoked": False}
        # Sem projeção ainda (nunca usou IA) cria o marcador: um contexto emitido antes da
        # remoção e usado depois não pode criar a pessoa de volta.
        user = conn.execute(
            """INSERT INTO users (tenant_id, chatwoot_user_id, email, name, is_active, revoked_at)
               VALUES (%s, %s, %s, %s, false, to_timestamp(COALESCE(%s, extract(epoch FROM now()))))
               ON CONFLICT (tenant_id, chatwoot_user_id) WHERE chatwoot_user_id IS NOT NULL
               DO UPDATE SET is_active = false, updated_at = now(),
                             revoked_at = EXCLUDED.revoked_at
               RETURNING id""",
            (
                tenant["id"],
                payload.chatwoot_user_id,
                f"cw{payload.chatwoot_user_id}@invalid.local",
                "Usuário removido",
                payload.revoked_at,
            ),
        ).fetchone()
        conn.execute(
            "UPDATE sessions SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL",
            (user["id"],),
        )
    from app.routes.chats import cancel_tenant_runs

    cancelled = await cancel_tenant_runs(tenant["id"], user["id"])
    return {
        "status": "ok",
        "revoked": True,
        "user_id": str(user["id"]),
        "runs_cancelled": cancelled,
    }


@router.post("/resume")
def resume(payload: AccountRef, actor: dict = Depends(install_actor)):
    _need(actor, "lifecycle")
    tenant = _set_active(payload.account_id, True, False)
    _rm("POST", ":resume", tenant["id"], {"approver": "chatwoot-super-admin"})
    return {"status": "ok", "engine_tenant_id": str(tenant["id"])}


@router.post("/deprovision")
async def deprovision(payload: AccountRef, actor: dict = Depends(install_actor)):
    """Desliga a empresa. **Nunca apaga dados** (decisão do dono): fica inativa e preservada."""
    _need(actor, "lifecycle")
    tenant = _set_active(payload.account_id, False, True)
    from app.routes.chats import cancel_tenant_runs

    await cancel_tenant_runs(tenant["id"])
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
