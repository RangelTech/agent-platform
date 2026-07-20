"""Integration management (admin side). The API key is generated here,
shown once, stored hashed — same discipline as session tokens."""

import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, Field

from app.auth import require
from app.crypto import encrypt
from app.db import get_connection
from app.tenancy import resolve_target_tenant

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


class IntegrationIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    template_id: str | None = None
    webhook_url: str | None = None
    rate_limit_per_minute: int = Field(default=60, ge=1, le=6000)
    tenant_id: str | None = None  # master only


def _new_key() -> tuple[str, str, str]:
    plaintext = "ap_" + secrets.token_urlsafe(32)
    return plaintext, hashlib.sha256(plaintext.encode()).hexdigest(), plaintext[:10]


def _serialize(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "name": row["name"],
        "channel": row["channel"],
        "template_id": str(row["template_id"]) if row["template_id"] else None,
        "key_prefix": row["key_prefix"] + "…",
        "webhook_url": row["webhook_url"],
        "rate_limit_per_minute": row["rate_limit_per_minute"],
        "is_active": row["is_active"] and row["revoked_at"] is None,
        "created_at": row["created_at"].isoformat(),
    }


def _scoped(conn, integration_id: str, user: dict) -> dict:
    row = conn.execute(
        "SELECT * FROM integrations WHERE id = %s", (integration_id,)
    ).fetchone()
    if row is None or (
        not user["is_master"] and str(row["tenant_id"]) != str(user["tenant_id"])
    ):
        raise HTTPException(status_code=404, detail="Integração não encontrada")
    return row


@router.get("")
def list_integrations(user: dict = Depends(require("integrations", "view"))):
    with get_connection() as conn:
        scope = "" if user["is_master"] else " WHERE tenant_id = %s"
        params = () if user["is_master"] else (user["tenant_id"],)
        rows = conn.execute(
            f"SELECT * FROM integrations{scope} ORDER BY name", params
        ).fetchall()
    return [_serialize(r) for r in rows]


@router.post("", status_code=201)
def create_integration(
    payload: IntegrationIn, user: dict = Depends(require("integrations", "create"))
):
    tenant_id = resolve_target_tenant(user, payload.tenant_id)
    with get_connection() as conn:
        if payload.template_id:
            owned = conn.execute(
                "SELECT 1 FROM templates WHERE id = %s AND tenant_id = %s AND NOT is_deleted",
                (payload.template_id, tenant_id),
            ).fetchone()
            if owned is None:
                raise HTTPException(status_code=400, detail="Template não pertence ao tenant")

        plaintext, key_hash, prefix = _new_key()
        webhook_secret = secrets.token_urlsafe(24) if payload.webhook_url else None
        try:
            row = conn.execute(
                """INSERT INTO integrations
                       (tenant_id, name, template_id, api_key_hash, key_prefix,
                        webhook_url, webhook_secret_encrypted, rate_limit_per_minute)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
                (
                    tenant_id,
                    payload.name,
                    payload.template_id,
                    key_hash,
                    prefix,
                    payload.webhook_url,
                    encrypt(webhook_secret) if webhook_secret else None,
                    payload.rate_limit_per_minute,
                ),
            ).fetchone()
        except UniqueViolation as exc:
            raise HTTPException(
                status_code=409, detail="Já existe uma integração com esse nome"
            ) from exc
    body = _serialize(row)
    # Shown exactly once; only the hash is stored.
    body["api_key"] = plaintext
    if webhook_secret:
        body["webhook_secret"] = webhook_secret
    return body


@router.post("/{integration_id}/rotate")
def rotate_key(integration_id: str, user: dict = Depends(require("integrations", "edit"))):
    plaintext, key_hash, prefix = _new_key()
    with get_connection() as conn:
        _scoped(conn, integration_id, user)
        conn.execute(
            """UPDATE integrations
                  SET api_key_hash = %s, key_prefix = %s, revoked_at = NULL, is_active = TRUE
                WHERE id = %s""",
            (key_hash, prefix, integration_id),
        )
    return {"api_key": plaintext, "key_prefix": prefix + "…"}


@router.post("/{integration_id}/revoke")
def revoke(integration_id: str, user: dict = Depends(require("integrations", "edit"))):
    with get_connection() as conn:
        _scoped(conn, integration_id, user)
        conn.execute(
            "UPDATE integrations SET revoked_at = now(), is_active = FALSE WHERE id = %s",
            (integration_id,),
        )
    return {"status": "ok"}


@router.get("/{integration_id}/messages")
def list_messages(integration_id: str, user: dict = Depends(require("integrations", "view"))):
    with get_connection() as conn:
        _scoped(conn, integration_id, user)
        rows = conn.execute(
            """SELECT external_session_id, direction, content, created_at
                 FROM integration_messages
                WHERE integration_id = %s ORDER BY created_at DESC LIMIT 200""",
            (integration_id,),
        ).fetchall()
    return [
        {
            "external_session_id": r["external_session_id"],
            "direction": r["direction"],
            "content": r["content"][:500],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]
