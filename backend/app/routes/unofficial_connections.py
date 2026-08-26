"""Credenciais capturadas pelo RAtende Connector (produto-15) -- providers
"não oficiais" (Instagram/Facebook/TikTok, sessão de navegador) e providers
OAuth (Codex/Claude Code, 26/08/2026: a extensão mesma faz o PKCE e a troca
de token, sem app local nenhum -- ver ratende-connector/src/background/oauthFlow.ts).
Mesmo padrão de tenant-scoping/criptografia de `google_accounts.py`, mas
sem motor de OAuth aqui: o payload já chega pronto (cookies OU tokens,
dependendo do provider), o backend só cifra e guarda.

`cookies_encrypted` guarda o JSON cifrado independente do formato (cookies
de sessão ou tokens OAuth) -- é um blob opaco, o nome ficou de quando só
existiam providers de cookie; renomear é cosmético, não urgente.

Escopo desta fase (produto-15 §6c): só captura e armazenamento. Nenhum
consumo real (ler/mandar mensagem, usar o token OAuth) acontece aqui --
fica pra uma spec de integração depois que a captura for provada estável.
"""

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.auth import require
from app.crypto import encrypt
from app.db import get_connection
from app.tenancy import resolve_target_tenant

router = APIRouter(prefix="/api/unofficial-connections", tags=["unofficial-connections"])

_PROVIDERS_COOKIE = {"instagram_web", "facebook_web", "tiktok_web"}
_PROVIDERS_OAUTH = {"codex_cli", "claude_code"}
_PROVIDERS_VALIDOS = _PROVIDERS_COOKIE | _PROVIDERS_OAUTH


class CookieIn(BaseModel):
    name: str
    value: str
    domain: str
    path: str = "/"


class UnofficialConnectionIn(BaseModel):
    provider: str
    label: str = Field(min_length=1, max_length=120)
    external_label: str | None = Field(default=None, max_length=120)
    cookies: list[CookieIn] | None = None
    oauth_tokens: dict | None = None
    tenant_id: str | None = None  # master only

    @model_validator(mode="after")
    def _payload_bate_com_o_provider(self) -> "UnofficialConnectionIn":
        if self.provider in _PROVIDERS_COOKIE and not self.cookies:
            raise ValueError(f"provider {self.provider!r} exige 'cookies'")
        if self.provider in _PROVIDERS_OAUTH and not self.oauth_tokens:
            raise ValueError(f"provider {self.provider!r} exige 'oauth_tokens'")
        return self


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
    corpo = (
        [c.model_dump() for c in payload.cookies]
        if payload.provider in _PROVIDERS_COOKIE
        else payload.oauth_tokens
    )
    cookies_json = json.dumps(corpo)
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


class RenomearIn(BaseModel):
    label: str = Field(min_length=1, max_length=120)


@router.patch("/{connection_id}")
def renomear_unofficial_connection(
    connection_id: str,
    payload: RenomearIn,
    user: dict = Depends(require("unofficial_connections", "edit")),
):
    # 26/08/2026, pedido do dono: rotulo automatico (numero da conta) nao
    # e' bonito nem sempre identifica a conta -- deixa o usuario renomear
    # pra algo que ele reconheca (ex: "Instagram da loja"). So mexe no
    # label, nunca em cookies_encrypted/external_label.
    tenant_id = resolve_target_tenant(user, None)
    with get_connection() as conn:
        row = conn.execute(
            """UPDATE tenant_unofficial_connections SET label = %s, updated_at = now()
                WHERE id = %s AND tenant_id = %s AND is_active RETURNING *""",
            (payload.label, connection_id, tenant_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Conexão não encontrada")
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
