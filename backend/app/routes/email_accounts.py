"""Contas de email por tenant (produto-11) -- credencial das tools gerais
`email_list_inbox`/`email_send`. SMTP/IMAP com usuário/senha (ou senha de
app), não OAuth: cobre qualquer provedor sem registrar app por um a um,
decisão do dono (25/08/2026, "email pode ser via SMTP, é mais genérico").
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field

from app.auth import require
from app.crypto import decrypt, encrypt
from app.db import get_connection
from app.tenancy import resolve_target_tenant

router = APIRouter(prefix="/api/email-accounts", tags=["email-accounts"])


class EmailAccountIn(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    email_address: EmailStr
    smtp_host: str = Field(min_length=1, max_length=255)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    imap_host: str = Field(min_length=1, max_length=255)
    imap_port: int = Field(default=993, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1_000)
    use_tls: bool = True
    tenant_id: str | None = None  # master only


def _serialize(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "label": row["label"],
        "email_address": row["email_address"],
        "smtp_host": row["smtp_host"],
        "smtp_port": row["smtp_port"],
        "imap_host": row["imap_host"],
        "imap_port": row["imap_port"],
        "username": row["username"],
        "use_tls": row["use_tls"],
        "is_active": row["is_active"],
        "updated_at": row["updated_at"].isoformat(),
    }


@router.get("")
def list_email_accounts(user: dict = Depends(require("email_accounts", "view"))):
    tenant_id = resolve_target_tenant(user, None)
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM tenant_email_accounts
                WHERE tenant_id = %s AND is_active ORDER BY created_at""",
            (tenant_id,),
        ).fetchall()
    return [_serialize(r) for r in rows]


@router.post("", status_code=201)
def create_email_account(
    payload: EmailAccountIn, user: dict = Depends(require("email_accounts", "create"))
):
    tenant_id = resolve_target_tenant(user, payload.tenant_id)
    with get_connection() as conn:
        row = conn.execute(
            """INSERT INTO tenant_email_accounts
                   (tenant_id, label, email_address, smtp_host, smtp_port, imap_host,
                    imap_port, username, password_encrypted, use_tls)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING *""",
            (
                tenant_id,
                payload.label,
                payload.email_address,
                payload.smtp_host,
                payload.smtp_port,
                payload.imap_host,
                payload.imap_port,
                payload.username,
                encrypt(payload.password),
                payload.use_tls,
            ),
        ).fetchone()
    return _serialize(row)


@router.delete("/{account_id}", status_code=204)
def delete_email_account(
    account_id: str, user: dict = Depends(require("email_accounts", "delete"))
):
    tenant_id = resolve_target_tenant(user, None)
    with get_connection() as conn:
        row = conn.execute(
            """UPDATE tenant_email_accounts SET is_active = false
                WHERE id = %s AND tenant_id = %s RETURNING id""",
            (account_id, tenant_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Conta de email não encontrada")


def accounts_for_run_config(conn, tenant_id) -> list[dict]:
    """Usado por template_runtime.py -- credenciais decifradas, cruzam pro
    contexto de execução do kernel (mesmo padrão de `_payment_spec`)."""
    rows = conn.execute(
        """SELECT label, email_address, smtp_host, smtp_port, imap_host, imap_port,
                  username, password_encrypted, use_tls
             FROM tenant_email_accounts
            WHERE tenant_id = %s AND is_active""",
        (tenant_id,),
    ).fetchall()
    return [
        {
            "label": r["label"],
            "email_address": r["email_address"],
            "smtp_host": r["smtp_host"],
            "smtp_port": r["smtp_port"],
            "imap_host": r["imap_host"],
            "imap_port": r["imap_port"],
            "username": r["username"],
            "password": decrypt(r["password_encrypted"]),
            "use_tls": r["use_tls"],
        }
        for r in rows
    ]
