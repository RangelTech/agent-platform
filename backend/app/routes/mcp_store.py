"""MCP Store: catálogo curado pelo master + ativação por tenant.

O tenant não registra URL arbitrária de servidor MCP — escolhe um item que o
master publicou e informa só as credenciais dele. As credenciais seguem o
mesmo Fernet do resto da plataforma e são resolvidas apenas em
`template_runtime`, na hora de montar o payload do kernel.
"""

import json
import re

from fastapi import APIRouter, Depends, HTTPException
from psycopg.errors import UniqueViolation
from psycopg.types.json import Json
from pydantic import BaseModel, Field

from app.auth import require
from app.crypto import decrypt, encrypt
from app.db import get_connection
from app.tenancy import resolve_target_tenant

router = APIRouter(prefix="/api/mcp-store", tags=["mcp-store"])

_SLUG = re.compile(r"^[a-z0-9_]{1,60}$")


class CredentialField(BaseModel):
    key: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,60}$")
    label: str = Field(min_length=1, max_length=120)
    secret: bool = True


class CatalogItemIn(BaseModel):
    slug: str
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    category: str = "geral"
    icon: str = ""
    server_url: str = ""
    auth_token_template: str = ""
    required_credentials: list[CredentialField] = Field(default_factory=list)
    is_active: bool = True


class ActivationIn(BaseModel):
    credentials: dict[str, str] = Field(default_factory=dict)
    template_ids: list[str] = Field(default_factory=list)
    is_active: bool = True
    tenant_id: str | None = None  # master only


def _serialize_item(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "slug": row["slug"],
        "name": row["name"],
        "description": row["description"],
        "category": row["category"],
        "icon": row["icon"],
        "server_url": row["server_url"],
        "required_credentials": row["required_credentials"],
        "is_native": row["is_native"],
        "native_key": row["native_key"],
        "is_active": row["is_active"],
    }


def _serialize_activation(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "item_id": str(row["item_id"]),
        "tenant_id": str(row["tenant_id"]),
        # Só os nomes dos campos preenchidos; valores nunca voltam.
        "configured_fields": sorted(json.loads(decrypt(row["credentials_encrypted"]) or "{}"))
        if row["credentials_encrypted"]
        else [],
        "template_ids": [str(t) for t in (row["template_ids"] or [])],
        "is_active": row["is_active"],
        "updated_at": row["updated_at"].isoformat(),
    }


def _master_only(user: dict) -> None:
    if not user.get("is_master"):
        raise HTTPException(status_code=403, detail="Somente o administrador da plataforma")


@router.get("/catalog")
def list_catalog(user: dict = Depends(require("mcp_store", "view"))):
    """Master vê tudo (inclusive itens desativados); tenant só o que está no ar."""
    with get_connection() as conn:
        scope = "" if user["is_master"] else " WHERE is_active"
        rows = conn.execute(
            f"SELECT * FROM mcp_catalog_items{scope} ORDER BY category, name"
        ).fetchall()
    return [_serialize_item(r) for r in rows]


@router.post("/catalog", status_code=201)
def create_item(payload: CatalogItemIn, user: dict = Depends(require("mcp_store", "create"))):
    _master_only(user)
    if not _SLUG.match(payload.slug):
        raise HTTPException(status_code=400, detail="Slug inválido (a-z, 0-9, _)")
    if not payload.server_url:
        raise HTTPException(status_code=400, detail="Informe a URL do servidor MCP")
    with get_connection() as conn:
        try:
            row = conn.execute(
                """INSERT INTO mcp_catalog_items
                       (slug, name, description, category, icon, server_url,
                        auth_token_template, required_credentials, is_active)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
                (
                    payload.slug,
                    payload.name,
                    payload.description,
                    payload.category,
                    payload.icon,
                    payload.server_url,
                    payload.auth_token_template,
                    Json([c.model_dump() for c in payload.required_credentials]),
                    payload.is_active,
                ),
            ).fetchone()
        except UniqueViolation as exc:
            raise HTTPException(status_code=409, detail="Já existe um item com esse slug") from exc
    return _serialize_item(row)


@router.put("/catalog/{item_id}")
def update_item(
    item_id: str, payload: CatalogItemIn, user: dict = Depends(require("mcp_store", "edit"))
):
    _master_only(user)
    with get_connection() as conn:
        row = conn.execute(
            """UPDATE mcp_catalog_items
                  SET name = %s, description = %s, category = %s, icon = %s,
                      server_url = %s, auth_token_template = %s,
                      required_credentials = %s, is_active = %s, updated_at = now()
                WHERE id = %s RETURNING *""",
            (
                payload.name,
                payload.description,
                payload.category,
                payload.icon,
                payload.server_url,
                payload.auth_token_template,
                Json([c.model_dump() for c in payload.required_credentials]),
                payload.is_active,
                item_id,
            ),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Item não encontrado")
    return _serialize_item(row)


@router.delete("/catalog/{item_id}")
def deactivate_item(item_id: str, user: dict = Depends(require("mcp_store", "delete"))):
    """Desativa em vez de apagar: tenants que já ativaram não perdem o registro."""
    _master_only(user)
    with get_connection() as conn:
        row = conn.execute(
            """UPDATE mcp_catalog_items SET is_active = FALSE, updated_at = now()
                WHERE id = %s RETURNING id""",
            (item_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Item não encontrado")
    return {"status": "ok"}


@router.get("/activations")
def list_activations(user: dict = Depends(require("mcp_store", "view"))):
    with get_connection() as conn:
        scope = "" if user["is_master"] else " WHERE tenant_id = %s"
        params = () if user["is_master"] else (user["tenant_id"],)
        rows = conn.execute(
            f"SELECT * FROM tenant_mcp_activations{scope} ORDER BY updated_at DESC", params
        ).fetchall()
    return [_serialize_activation(r) for r in rows]


@router.put("/activations/{item_id}")
def activate(
    item_id: str, payload: ActivationIn, user: dict = Depends(require("mcp_store", "edit"))
):
    tenant_id = resolve_target_tenant(user, payload.tenant_id)
    with get_connection() as conn:
        item = conn.execute(
            "SELECT * FROM mcp_catalog_items WHERE id = %s AND is_active", (item_id,)
        ).fetchone()
        if item is None:
            raise HTTPException(status_code=404, detail="Item do catálogo não encontrado")
        if item["is_native"]:
            raise HTTPException(
                status_code=400,
                detail="Item nativo: configure-o na própria tela da funcionalidade",
            )

        existing = conn.execute(
            "SELECT * FROM tenant_mcp_activations WHERE tenant_id = %s AND item_id = %s",
            (tenant_id, item_id),
        ).fetchone()
        stored = (
            json.loads(decrypt(existing["credentials_encrypted"]) or "{}")
            if existing and existing["credentials_encrypted"]
            else {}
        )
        # Campo em branco preserva o valor já salvo — credenciais nunca voltam
        # pela API, então "não mandou" tem que significar "não mexeu".
        merged = {**stored, **{k: v for k, v in payload.credentials.items() if v}}

        missing = [
            field["key"]
            for field in (item["required_credentials"] or [])
            if not merged.get(field["key"])
        ]
        if missing:
            raise HTTPException(
                status_code=400, detail=f"Credenciais obrigatórias faltando: {', '.join(missing)}"
            )

        # Só templates do próprio tenant podem ser alvo da ativação.
        if payload.template_ids:
            owned = conn.execute(
                """SELECT id FROM templates
                    WHERE id = ANY(%s) AND tenant_id = %s AND NOT is_deleted""",
                (payload.template_ids, tenant_id),
            ).fetchall()
            if len(owned) != len(set(payload.template_ids)):
                raise HTTPException(status_code=404, detail="Template não encontrado")

        row = conn.execute(
            """INSERT INTO tenant_mcp_activations
                   (tenant_id, item_id, credentials_encrypted, template_ids, is_active)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (tenant_id, item_id) DO UPDATE
                   SET credentials_encrypted = EXCLUDED.credentials_encrypted,
                       template_ids = EXCLUDED.template_ids,
                       is_active = EXCLUDED.is_active,
                       updated_at = now()
               RETURNING *""",
            (
                tenant_id,
                item_id,
                encrypt(json.dumps(merged)) if merged else None,
                payload.template_ids,
                payload.is_active,
            ),
        ).fetchone()
    return _serialize_activation(row)


@router.delete("/activations/{item_id}")
def deactivate(item_id: str, user: dict = Depends(require("mcp_store", "delete"))):
    with get_connection() as conn:
        scope = "" if user["is_master"] else " AND tenant_id = %s"
        params = (item_id,) if user["is_master"] else (item_id, user["tenant_id"])
        row = conn.execute(
            f"""UPDATE tenant_mcp_activations SET is_active = FALSE, updated_at = now()
                 WHERE item_id = %s{scope} RETURNING id""",
            params,
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Ativação não encontrada")
    return {"status": "ok"}
