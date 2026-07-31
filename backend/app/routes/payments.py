"""Credenciais de gateway de pagamento por tenant + webhook de confirmação.

Escopo desta fase: Mercado Pago, PIX. O access token é write-only e fica
criptografado em repouso (mesmo Fernet de datasources/ai_services). O webhook
é público por natureza — quem chama é o Mercado Pago — então a autenticação
dele é o par (webhook_token opaco na URL, assinatura x-signature HMAC).
"""

import hashlib
import hmac
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.types.json import Json
from pydantic import BaseModel, Field

from app.auth import require
from app.config import settings
from app.crypto import decrypt, encrypt
from app.db import get_connection
from app.tenancy import resolve_target_tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/payments", tags=["payments"])

PROVIDER = "mercado_pago"

# Status do Mercado Pago -> status interno.
_STATUS_MAP = {
    "approved": "paid",
    "authorized": "pending",
    "in_process": "pending",
    "in_mediation": "pending",
    "pending": "pending",
    "rejected": "failed",
    "cancelled": "cancelled",
    "refunded": "refunded",
    "charged_back": "refunded",
}


class CredentialIn(BaseModel):
    access_token: str | None = Field(default=None, min_length=1, max_length=5_000)
    webhook_secret: str | None = Field(default=None, max_length=5_000)
    sandbox: bool = True
    is_active: bool = True
    tenant_id: str | None = None  # master only


def _serialize_credential(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "provider": row["provider"],
        "has_token": bool(row["access_token_encrypted"]),
        "has_webhook_secret": bool(row["webhook_secret_encrypted"]),
        "sandbox": row["sandbox"],
        "is_active": row["is_active"],
        # Só o sufixo opaco é exposto; a URL completa é montada pelo frontend.
        "webhook_path": f"/api/payments/webhooks/mercado-pago/{row['webhook_token']}",
        "updated_at": row["updated_at"].isoformat(),
    }


def _serialize_charge(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "external_id": row["external_id"],
        "amount": float(row["amount"]),
        "description": row["description"],
        "reference_id": row["reference_id"],
        "status": row["status"],
        "sandbox": row["sandbox"],
        "ticket_url": row["ticket_url"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


@router.get("/credentials")
def get_credential(user: dict = Depends(require("payments", "view"))):
    with get_connection() as conn:
        scope = "" if user["is_master"] else " AND tenant_id = %s"
        params = (PROVIDER,) if user["is_master"] else (PROVIDER, user["tenant_id"])
        rows = conn.execute(
            f"""SELECT * FROM payment_credentials WHERE provider = %s{scope}
                 ORDER BY updated_at DESC""",
            params,
        ).fetchall()
    return [_serialize_credential(r) for r in rows]


@router.put("/credentials")
def upsert_credential(payload: CredentialIn, user: dict = Depends(require("payments", "edit"))):
    tenant_id = resolve_target_tenant(user, payload.tenant_id)
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT * FROM payment_credentials WHERE tenant_id = %s AND provider = %s",
            (tenant_id, PROVIDER),
        ).fetchone()
        if existing is None:
            if not payload.access_token:
                raise HTTPException(
                    status_code=400, detail="Informe o access token do Mercado Pago"
                )
            row = conn.execute(
                """INSERT INTO payment_credentials
                       (tenant_id, provider, access_token_encrypted,
                        webhook_secret_encrypted, sandbox, is_active)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
                (
                    tenant_id,
                    PROVIDER,
                    encrypt(payload.access_token),
                    encrypt(payload.webhook_secret) if payload.webhook_secret else None,
                    payload.sandbox,
                    payload.is_active,
                ),
            ).fetchone()
            return _serialize_credential(row)

        fields = ["sandbox = %s", "is_active = %s", "updated_at = now()"]
        values: list = [payload.sandbox, payload.is_active]
        # Campos em branco preservam o valor atual — token nunca volta em claro
        # pela API, então "não mandou" tem que significar "não mexeu".
        if payload.access_token:
            fields.insert(0, "access_token_encrypted = %s")
            values.insert(0, encrypt(payload.access_token))
        if payload.webhook_secret is not None:
            fields.append("webhook_secret_encrypted = %s")
            values.append(encrypt(payload.webhook_secret) if payload.webhook_secret else None)
        values.append(existing["id"])
        row = conn.execute(
            f"UPDATE payment_credentials SET {', '.join(fields)} WHERE id = %s RETURNING *",
            tuple(values),
        ).fetchone()
    return _serialize_credential(row)


@router.delete("/credentials/{credential_id}")
def delete_credential(credential_id: str, user: dict = Depends(require("payments", "delete"))):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM payment_credentials WHERE id = %s", (credential_id,)
        ).fetchone()
        if row is None or (
            not user["is_master"] and str(row["tenant_id"]) != str(user["tenant_id"])
        ):
            raise HTTPException(status_code=404, detail="Credencial não encontrada")
        conn.execute("DELETE FROM payment_credentials WHERE id = %s", (credential_id,))
    return {"status": "ok"}


@router.get("/charges")
def list_charges(user: dict = Depends(require("payments", "view"))):
    with get_connection() as conn:
        scope = "" if user["is_master"] else " WHERE tenant_id = %s"
        params = () if user["is_master"] else (user["tenant_id"],)
        rows = conn.execute(
            f"SELECT * FROM payment_charges{scope} ORDER BY created_at DESC LIMIT 100",
            params,
        ).fetchall()
    return [_serialize_charge(r) for r in rows]


def _signature_ok(request: Request, secret: str, data_id: str) -> bool:
    """Valida o header x-signature do Mercado Pago.

    Formato: `ts=<unix>,v1=<hmac_sha256>`; o manifesto assinado é
    `id:<data.id>;request-id:<x-request-id>;ts:<ts>;`.
    """
    header = request.headers.get("x-signature", "")
    parts = dict(
        piece.split("=", 1) for piece in header.split(",") if "=" in piece
    )
    ts, received = parts.get("ts", "").strip(), parts.get("v1", "").strip()
    if not ts or not received:
        return False
    manifest = f"id:{data_id};request-id:{request.headers.get('x-request-id', '')};ts:{ts};"
    expected = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


@router.post("/webhooks/mercado-pago/{webhook_token}")
async def mercado_pago_webhook(webhook_token: str, request: Request):
    """Confirmação assíncrona de pagamento.

    O corpo do Mercado Pago só traz o id — o valor e o status vêm de uma
    releitura autenticada na API deles, para que um webhook forjado não
    consiga marcar nada como pago.
    """
    with get_connection() as conn:
        credential = conn.execute(
            "SELECT * FROM payment_credentials WHERE webhook_token = %s AND is_active",
            (webhook_token,),
        ).fetchone()
    if credential is None:
        raise HTTPException(status_code=404, detail="webhook desconhecido")

    body = await request.json() if await request.body() else {}
    data_id = str((body.get("data") or {}).get("id") or body.get("id") or "")
    if not data_id:
        return {"status": "ignored"}

    if credential["webhook_secret_encrypted"]:
        secret = decrypt(credential["webhook_secret_encrypted"])
        if not _signature_ok(request, secret, data_id):
            raise HTTPException(status_code=401, detail="assinatura inválida")

    token = decrypt(credential["access_token_encrypted"] or "") or ""
    if not token:
        raise HTTPException(status_code=409, detail="credencial sem access token")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{settings.mercado_pago_api}/v1/payments/{data_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            payment = response.json()
    except httpx.HTTPError as exc:
        logger.warning("falha ao reler pagamento %s: %s", data_id, exc)
        raise HTTPException(status_code=502, detail="gateway indisponível") from exc

    status = _STATUS_MAP.get(payment.get("status", ""), "pending")
    with get_connection() as conn:
        updated = conn.execute(
            """UPDATE payment_charges
                  SET status = %s, raw = %s, updated_at = now()
                WHERE provider = %s AND external_id = %s AND tenant_id = %s
            RETURNING *""",
            (status, Json(payment), PROVIDER, str(payment.get("id")), credential["tenant_id"]),
        ).fetchone()
    if updated is None:
        # Pagamento que não nasceu aqui: nada a confirmar, mas não é erro.
        return {"status": "unknown_charge"}
    return {"status": "ok", "charge_status": status}
