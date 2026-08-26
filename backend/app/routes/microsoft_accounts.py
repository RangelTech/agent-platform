"""Conta Microsoft por tenant (produto-08 §12) -- credencial das tools
`outlook_calendar_list_events`/`create_event` (agenda + reunião do Teams).

Mesmo motor de OAuth do `google_accounts.py` (`oauth_engine.py`, fluxo
`redirect_microsoft`), aqui com client_id/secret PRÓPRIO
(MS_OAUTH_CLIENT_ID/SECRET) e escopos Calendars.ReadWrite +
OnlineMeetings.ReadWrite. Token renovado sob demanda com lock por linha
(SELECT ... FOR UPDATE), nunca cron/estado em memória -- mesmo desenho de
`tenant_google_accounts`.

Diferente de `google_accounts.py` original (corrigido só depois no §9): esta
tabela já nasce multi-conta -- `accounts_for_run_config` sempre devolve a
lista inteira, nunca "só a mais recente".
"""

import datetime as dt

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import oauth_engine
from app.auth import require
from app.config import settings
from app.crypto import decrypt, encrypt
from app.db import get_connection
from app.tenancy import resolve_target_tenant

router = APIRouter(prefix="/api/microsoft-accounts", tags=["microsoft-accounts"])

_REFRESH_MARGIN = dt.timedelta(seconds=60)


def _serialize(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "label": row["label"],
        "email_address": row["email_address"],
        "connected": True,
        "updated_at": row["updated_at"].isoformat(),
    }


@router.get("")
def list_microsoft_accounts(user: dict = Depends(require("microsoft_accounts", "view"))):
    tenant_id = resolve_target_tenant(user, None)
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM tenant_microsoft_accounts
                WHERE tenant_id = %s AND is_active ORDER BY created_at""",
            (tenant_id,),
        ).fetchall()
    return [_serialize(r) for r in rows]


@router.delete("/{account_id}", status_code=204)
def delete_microsoft_account(
    account_id: str, user: dict = Depends(require("microsoft_accounts", "delete"))
):
    tenant_id = resolve_target_tenant(user, None)
    with get_connection() as conn:
        row = conn.execute(
            """UPDATE tenant_microsoft_accounts SET is_active = false
                WHERE id = %s AND tenant_id = %s RETURNING id""",
            (account_id, tenant_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Conta Microsoft não encontrada")


@router.post("/oauth/iniciar")
def oauth_iniciar(user: dict = Depends(require("microsoft_accounts", "create"))):
    redirect_uri = f"{settings.public_base_url.rstrip('/')}/oauth/callback"
    try:
        dados = oauth_engine.iniciar_redirect("microsoft-graph", redirect_uri)
    except oauth_engine.OAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "auth_url": dados["auth_url"],
        "redirect_uri": dados["redirect_uri"],
        "state": dados["state"],
    }


class OAuthFimIn(BaseModel):
    code: str
    redirect_uri: str
    label: str | None = None
    tenant_id: str | None = None  # master only


@router.post("/oauth/concluir", status_code=201)
async def oauth_concluir(
    payload: OAuthFimIn, user: dict = Depends(require("microsoft_accounts", "create"))
):
    tenant_id = resolve_target_tenant(user, payload.tenant_id)
    try:
        resultado = await oauth_engine.concluir_redirect(
            "microsoft-graph", payload.code, payload.redirect_uri, ""
        )
    except oauth_engine.OAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    expires_at = (
        dt.datetime.now(dt.UTC) + dt.timedelta(seconds=resultado.expires_in)
        if resultado.expires_in
        else None
    )
    label = (payload.label or "").strip() or resultado.email or "Microsoft"
    with get_connection() as conn:
        row = conn.execute(
            """INSERT INTO tenant_microsoft_accounts
                   (tenant_id, label, email_address, access_token_encrypted,
                    refresh_token_encrypted, token_expires_at, scope)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               RETURNING *""",
            (
                tenant_id,
                label,
                resultado.email,
                encrypt(resultado.access_token),
                encrypt(resultado.refresh_token) if resultado.refresh_token else None,
                expires_at,
                oauth_engine.MICROSOFT_GRAPH["scopes"],
            ),
        ).fetchone()
    return _serialize(row)


def _renovar_sync(refresh_token: str) -> oauth_engine.ResultadoToken:
    """Mesma troca de `oauth_engine.renovar` (fluxo Microsoft), mas síncrona
    -- `template_runtime.build_run_payload` é chamado sem loop async ao
    redor, mesmo motivo do equivalente em `google_accounts.py`."""
    cfg = oauth_engine.MICROSOFT_GRAPH
    resp = httpx.post(
        cfg["token_url"],
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
        },
        headers={"Accept": "application/json"},
        timeout=20.0,
    )
    if resp.status_code >= 400:
        raise oauth_engine.OAuthError(f"renovação falhou (microsoft-graph): {resp.text[:300]}")
    dados = resp.json()
    return oauth_engine.ResultadoToken(
        access_token=dados["access_token"],
        refresh_token=dados.get("refresh_token", refresh_token),
        expires_in=dados.get("expires_in"),
    )


def _account_for_run_config(conn, row: dict) -> dict:
    access_token = decrypt(row["access_token_encrypted"])
    expires_at = row["token_expires_at"]
    needs_refresh = (
        expires_at is not None and dt.datetime.now(dt.UTC) >= expires_at - _REFRESH_MARGIN
    )
    if needs_refresh and row["refresh_token_encrypted"]:
        try:
            resultado = _renovar_sync(decrypt(row["refresh_token_encrypted"]))
        except oauth_engine.OAuthError as exc:
            conn.execute(
                "UPDATE tenant_microsoft_accounts SET token_last_refresh_error = %s WHERE id = %s",
                (str(exc)[:500], row["id"]),
            )
        else:
            access_token = resultado.access_token
            new_refresh = (
                encrypt(resultado.refresh_token)
                if resultado.refresh_token
                else row["refresh_token_encrypted"]
            )
            new_expires_at = (
                dt.datetime.now(dt.UTC) + dt.timedelta(seconds=resultado.expires_in)
                if resultado.expires_in
                else None
            )
            conn.execute(
                """UPDATE tenant_microsoft_accounts
                       SET access_token_encrypted = %s, refresh_token_encrypted = %s,
                           token_expires_at = %s, token_last_refresh_error = NULL,
                           updated_at = now()
                     WHERE id = %s""",
                (encrypt(access_token), new_refresh, new_expires_at, row["id"]),
            )

    return {
        "label": row["label"],
        "access_token": access_token,
        "email_address": row["email_address"],
    }


def accounts_for_run_config(conn, tenant_id) -> list[dict]:
    """Usado por template_runtime.py -- TODAS as contas Microsoft ativas do
    tenant, cada uma renovada sob demanda."""
    rows = conn.execute(
        """SELECT * FROM tenant_microsoft_accounts
             WHERE tenant_id = %s AND is_active
             ORDER BY created_at FOR UPDATE""",
        (tenant_id,),
    ).fetchall()
    return [_account_for_run_config(conn, row) for row in rows]
