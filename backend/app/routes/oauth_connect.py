"""Conexão OAuth (Google/Outlook) fechada pelo servidor.

O navegador que inicia a conexão pode estar em outro domínio que o do motor (a tela de
credenciais do Luar), então o repasse do código por `postMessage` não chega. Aqui o motor guarda
o `state` pendente (tenant + nome) e a página de retorno `/oauth/callback` só entrega
`state` + `code` para `POST /api/oauth/finalizar`, que troca o código, grava a conta e encerra.
O `state` é aleatório, de uso único e vale 15 minutos.
"""

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import oauth_engine
from app.auth import current_user
from app.config import settings
from app.crypto import encrypt
from app.db import get_connection
from app.permissions import has_permission
from app.tenancy import resolve_target_tenant

router = APIRouter(tags=["oauth-connect"])

PENDING_TTL = dt.timedelta(minutes=15)

# apelido da tela -> (provedor do oauth_engine, tabela, recurso de permissão)
PROVIDERS = {
    "google": ("google-workspace", "tenant_google_accounts", "google_accounts"),
    "microsoft": ("microsoft-graph", "tenant_microsoft_accounts", "microsoft_accounts"),
}
_BY_ENGINE = {v[0]: v for v in PROVIDERS.values()}


class StartIn(BaseModel):
    label: str | None = Field(default=None, max_length=120)


@router.post("/api/credentials/oauth/{provider}/iniciar")
def oauth_iniciar(provider: str, payload: StartIn, user: dict = Depends(current_user)):
    if provider not in PROVIDERS:
        raise HTTPException(status_code=404, detail="Provedor desconhecido")
    engine_provider, _table, resource = PROVIDERS[provider]
    if not has_permission(user, resource, "create"):
        raise HTTPException(status_code=403, detail="Permissão negada")
    tenant_id = resolve_target_tenant(user, None)
    if not settings.public_base_url:
        raise HTTPException(status_code=503, detail="Endereço público do motor não configurado")
    redirect_uri = f"{settings.public_base_url.rstrip('/')}/oauth/callback"
    try:
        dados = oauth_engine.iniciar_redirect(engine_provider, redirect_uri)
    except oauth_engine.OAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if "client_id=&" in dados["auth_url"] or dados["auth_url"].endswith("client_id="):
        raise HTTPException(
            status_code=503, detail="A conexão com este serviço ainda não foi configurada"
        )
    with get_connection() as conn:
        conn.execute("DELETE FROM oauth_pending WHERE created_at < now() - interval '1 day'")
        conn.execute(
            """INSERT INTO oauth_pending (state, tenant_id, provider, label, redirect_uri)
               VALUES (%s, %s, %s, %s, %s)""",
            (
                dados["state"],
                tenant_id,
                engine_provider,
                (payload.label or "").strip() or None,
                dados["redirect_uri"],
            ),
        )
    return {"auth_url": dados["auth_url"], "state": dados["state"]}


class FinishIn(BaseModel):
    state: str = Field(min_length=8, max_length=200)
    code: str = Field(min_length=1, max_length=4000)


@router.post("/api/oauth/finalizar", status_code=201)
async def oauth_finalizar(payload: FinishIn):
    """Pública de propósito: quem chega aqui é o redirecionamento do provedor, sem sessão."""
    with get_connection() as conn:
        row = conn.execute(
            "DELETE FROM oauth_pending WHERE state = %s RETURNING *", (payload.state,)
        ).fetchone()
    if row is None or dt.datetime.now(dt.UTC) - row["created_at"] > PENDING_TTL:
        raise HTTPException(status_code=404, detail="Conexão não encontrada ou expirada")
    engine_provider, table, _resource = _BY_ENGINE[row["provider"]]
    try:
        resultado = await oauth_engine.concluir_redirect(
            engine_provider, payload.code, row["redirect_uri"], ""
        )
    except oauth_engine.OAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    expires_at = (
        dt.datetime.now(dt.UTC) + dt.timedelta(seconds=resultado.expires_in)
        if resultado.expires_in
        else None
    )
    google = engine_provider == "google-workspace"
    cfg = oauth_engine.GOOGLE_WORKSPACE if google else oauth_engine.MICROSOFT_GRAPH
    label = row["label"] or resultado.email or ("Google" if google else "Outlook")
    with get_connection() as conn:
        conn.execute(
            f"""INSERT INTO {table}
                    (tenant_id, label, email_address, access_token_encrypted,
                     refresh_token_encrypted, token_expires_at, scope)
                VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                row["tenant_id"],
                label,
                resultado.email,
                encrypt(resultado.access_token),
                encrypt(resultado.refresh_token) if resultado.refresh_token else None,
                expires_at,
                cfg["scopes"],
            ),
        )
    return {"status": "ok", "label": label}
