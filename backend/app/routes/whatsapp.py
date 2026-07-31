"""Canal WhatsApp (W-API): conexão por integração + webhook de entrada.

O webhook é só uma segunda porta de entrada para o pipeline que a API pública
já usa: nada de lógica de negócio duplicada — `_build_kernel_payload`,
`_run_to_completion` e `_log_message` são reaproveitados de `public_api`.
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app import wapi_client
from app.auth import require
from app.crypto import decrypt, encrypt
from app.db import get_connection
from app.routes.public_api import (
    PublicMessageIn,
    _build_kernel_payload,
    _log_message,
    _run_to_completion,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["whatsapp"])


class ConnectionIn(BaseModel):
    instance_id: str = Field(min_length=1, max_length=200)
    token: str | None = Field(default=None, max_length=5_000)
    api_base: str = Field(default="https://api.w-api.app/v1", max_length=300)
    is_active: bool = True


def _serialize(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "integration_id": str(row["integration_id"]),
        "instance_id": row["instance_id"],
        "api_base": row["api_base"],
        "has_token": bool(row["token_encrypted"]),
        "is_active": row["is_active"],
        "last_test_at": row["last_test_at"].isoformat() if row["last_test_at"] else None,
        "last_test_ok": row["last_test_ok"],
        "webhook_path": f"/webhooks/whatsapp/{row['integration_id']}",
    }


def _scoped_integration(conn, integration_id: str, user: dict) -> dict:
    row = conn.execute(
        "SELECT * FROM integrations WHERE id = %s", (integration_id,)
    ).fetchone()
    if row is None or (
        not user["is_master"] and str(row["tenant_id"]) != str(user["tenant_id"])
    ):
        raise HTTPException(status_code=404, detail="Integração não encontrada")
    return row


def _connection_secrets(row: dict) -> dict:
    """Linha do banco -> dicionário que o cliente da W-API entende."""
    return {
        "instance_id": row["instance_id"],
        "token": decrypt(row["token_encrypted"]),
        "api_base": row["api_base"],
    }


@router.get("/api/integrations/{integration_id}/whatsapp")
def read_connection(
    integration_id: str, user: dict = Depends(require("integrations", "view"))
):
    with get_connection() as conn:
        _scoped_integration(conn, integration_id, user)
        row = conn.execute(
            "SELECT * FROM whatsapp_connections WHERE integration_id = %s",
            (integration_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Conexão não configurada")
    return _serialize(row)


@router.put("/api/integrations/{integration_id}/whatsapp")
def upsert_connection(
    integration_id: str,
    payload: ConnectionIn,
    user: dict = Depends(require("integrations", "edit")),
):
    with get_connection() as conn:
        integration = _scoped_integration(conn, integration_id, user)
        if integration["channel"] != "whatsapp":
            raise HTTPException(
                status_code=400, detail="Esta integração não é do canal WhatsApp"
            )
        existing = conn.execute(
            "SELECT * FROM whatsapp_connections WHERE integration_id = %s",
            (integration_id,),
        ).fetchone()
        if existing is None and not payload.token:
            raise HTTPException(status_code=400, detail="Informe o token da instância W-API")

        # Token em branco preserva o já salvo — ele nunca volta pela API.
        token_encrypted = encrypt(payload.token) if payload.token else existing["token_encrypted"]
        row = conn.execute(
            """INSERT INTO whatsapp_connections
                   (tenant_id, integration_id, instance_id, token_encrypted, api_base, is_active)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (integration_id) DO UPDATE
                   SET instance_id = EXCLUDED.instance_id,
                       token_encrypted = EXCLUDED.token_encrypted,
                       api_base = EXCLUDED.api_base,
                       is_active = EXCLUDED.is_active,
                       updated_at = now()
               RETURNING *""",
            (
                integration["tenant_id"],
                integration_id,
                payload.instance_id,
                token_encrypted,
                payload.api_base,
                payload.is_active,
            ),
        ).fetchone()
    return _serialize(row)


@router.post("/api/integrations/{integration_id}/whatsapp/test")
async def test_connection(
    integration_id: str, user: dict = Depends(require("integrations", "edit"))
):
    """Valida credencial contra a W-API sem mandar mensagem para ninguém."""
    with get_connection() as conn:
        _scoped_integration(conn, integration_id, user)
        row = conn.execute(
            "SELECT * FROM whatsapp_connections WHERE integration_id = %s",
            (integration_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Conexão não configurada")

    ok, detail = False, ""
    try:
        body = await wapi_client.check_status(_connection_secrets(row))
        ok = True
        detail = str(body)[:300]
    except wapi_client.WapiError as exc:
        detail = str(exc)

    with get_connection() as conn:
        conn.execute(
            """UPDATE whatsapp_connections
                  SET last_test_at = now(), last_test_ok = %s, updated_at = now()
                WHERE id = %s""",
            (ok, row["id"]),
        )
    return {"ok": ok, "detail": detail}


def _record_event(integration_id: str | None, raw: str, status: str, detail: str = "") -> None:
    try:
        with get_connection() as conn:
            conn.execute(
                """INSERT INTO whatsapp_webhook_events
                       (integration_id, raw_body, status, detail)
                   VALUES (%s, %s, %s, %s)""",
                (integration_id, raw[:20_000], status, detail[:1_000]),
            )
    except Exception:  # noqa: BLE001 — log do evento nunca pode derrubar o webhook
        logger.exception("falha ao registrar evento de webhook whatsapp")


def _ensure_channel_chat(tenant_id, template_id, phone: str) -> str:
    """Uma conversa por (tenant, telefone), visível no Chat como canal WhatsApp."""
    with get_connection() as conn:
        row = conn.execute(
            """SELECT id FROM chats
                WHERE tenant_id = %s AND channel = 'whatsapp' AND external_contact = %s
                ORDER BY updated_at DESC LIMIT 1""",
            (tenant_id, phone),
        ).fetchone()
        if row is not None:
            return str(row["id"])
        created = conn.execute(
            """INSERT INTO chats (tenant_id, user_id, title, template_id, channel, external_contact)
               VALUES (%s, NULL, %s, %s, 'whatsapp', %s) RETURNING id""",
            (tenant_id, f"WhatsApp {phone}", template_id, phone),
        ).fetchone()
    return str(created["id"])


def _append_chat_message(chat_id: str, role: str, content: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO chat_messages (chat_id, role, content) VALUES (%s, %s, %s)",
            (chat_id, role, content[:32_000]),
        )
        conn.execute("UPDATE chats SET updated_at = now() WHERE id = %s", (chat_id,))


async def _process_message(integration: dict, connection: dict, message: dict) -> None:
    """Roda o agente e devolve a resposta pelo WhatsApp.

    Falha de entrega não pode derrubar o processamento: a resposta já foi
    gerada e fica registrada mesmo se a W-API estiver fora do ar.
    """
    phone, text = message["phone"], message["text"]
    session_id = f"wa-{phone}"
    integration_id = str(integration["id"])
    chat_id = None
    try:
        _log_message(integration["id"], session_id, "in", text)
        chat_id = _ensure_channel_chat(
            integration["tenant_id"], integration["template_id"], phone
        )
        _append_chat_message(chat_id, "user", text)

        payload = PublicMessageIn(message=text, external_session_id=session_id)
        kernel_payload = _build_kernel_payload(integration, payload)
        # A conversa do WhatsApp e a do Chat são a mesma thread no kernel.
        kernel_payload["thread_id"] = chat_id
        reply = await _run_to_completion(kernel_payload)
    except Exception as exc:  # noqa: BLE001 — o webhook já respondeu 200
        logger.exception("falha ao processar mensagem do WhatsApp")
        _record_event(integration_id, text, "failed", str(exc))
        return

    _log_message(integration["id"], session_id, "out", reply)
    if chat_id:
        _append_chat_message(chat_id, "assistant", reply)

    try:
        await wapi_client.send_message(connection, phone, reply)
        _record_event(integration_id, text, "delivered")
    except wapi_client.WapiError as exc:
        # Processado mas não entregue: fica registrado para reenvio manual.
        logger.warning("resposta não entregue no WhatsApp: %s", exc)
        _record_event(integration_id, text, "undelivered", str(exc))


@router.post("/webhooks/whatsapp/{integration_id}")
async def whatsapp_webhook(
    integration_id: str, request: Request, background: BackgroundTasks
):
    """Recebe eventos da W-API.

    Sempre responde 200: um erro aqui faria a W-API reenviar o mesmo evento em
    loop. O corpo cru é gravado antes de qualquer parsing.
    """
    raw = (await request.body()).decode("utf-8", errors="replace")

    with get_connection() as conn:
        integration = conn.execute(
            """SELECT i.* FROM integrations i
                 JOIN tenants t ON t.id = i.tenant_id
                WHERE i.id = %s AND i.is_active AND i.revoked_at IS NULL
                  AND i.channel = 'whatsapp' AND t.is_active""",
            (integration_id,),
        ).fetchone()
        connection_row = (
            conn.execute(
                "SELECT * FROM whatsapp_connections WHERE integration_id = %s AND is_active",
                (integration_id,),
            ).fetchone()
            if integration is not None
            else None
        )

    if integration is None or connection_row is None:
        _record_event(None, raw, "unknown_integration")
        return {"status": "ignored"}

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 — corpo inválido não pode virar retry infinito
        _record_event(integration_id, raw, "unparseable")
        return {"status": "ignored"}

    message = wapi_client.extract_message(payload)
    if message is None:
        _record_event(integration_id, raw, "no_content")
        return {"status": "ignored"}

    _record_event(integration_id, raw, "accepted")
    # Responde já; o agente pode levar dezenas de segundos.
    background.add_task(
        _process_message, dict(integration), _connection_secrets(connection_row), message
    )
    return {"status": "accepted"}
