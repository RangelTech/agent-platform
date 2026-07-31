"""Cliente da W-API (gateway não-oficial de WhatsApp), por tenant.

Adaptado de `loki/channels/wapi.py`, com uma diferença central: o token e o
instance_id vêm da linha `whatsapp_connections` daquela integração, nunca de
configuração global — dois tenants nunca compartilham instância.
"""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TIMEOUT = 20.0


class WapiError(RuntimeError):
    pass


def _url(connection: dict, path: str) -> str:
    base = (connection.get("api_base") or "https://api.w-api.app/v1").rstrip("/")
    return f"{base}/{path.lstrip('/')}"


async def call(connection: dict, path: str, *, method: str = "POST", json_body: dict | None = None):
    """Chamada HTTP autenticada contra a W-API para a instância do tenant."""
    token = connection["token"]
    params = {"instanceId": connection["instance_id"]}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.request(
                method,
                _url(connection, path),
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                json=json_body,
            )
    except httpx.HTTPError as exc:
        raise WapiError(f"W-API inacessível: {exc}") from exc
    if response.status_code >= 400:
        raise WapiError(f"W-API respondeu {response.status_code}: {response.text[:300]}")
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text[:1000]}


async def check_status(connection: dict) -> dict:
    """Valida credencial sem enviar mensagem a ninguém."""
    return await call(connection, "instance/status", method="GET")


async def send_message(connection: dict, phone: str, text: str) -> dict:
    return await call(
        connection,
        "message/send-text",
        json_body={"phone": phone, "message": text},
    )


def extract_message(payload: dict[str, Any]) -> dict | None:
    """Normaliza o webhook da W-API em {phone, text, message_id}.

    Retorna None para o que não é mensagem de entrada com texto (status de
    entrega, confirmação de leitura, mensagem enviada pelo próprio número).
    Formato defensivo de propósito: a W-API é um serviço não-oficial e já
    mudou o envelope mais de uma vez.
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("fromMe") or (payload.get("key") or {}).get("fromMe"):
        return None

    # Eventos de status/entrega/leitura chegam no mesmo webhook das mensagens
    # e frequentemente trazem "message" no nome ("message.status"), então a
    # regra é negativa: qualquer coisa que cheire a status não é entrada.
    event = str(payload.get("event") or payload.get("type") or "").lower()
    if any(word in event for word in ("status", "ack", "read", "deliver", "receipt")):
        return None
    if event and "message" not in event and "chat" not in event:
        return None

    phone = (
        payload.get("phone")
        or payload.get("sender")
        or payload.get("from")
        or (payload.get("key") or {}).get("remoteJid")
        or ""
    )
    phone = str(phone).split("@")[0].strip()

    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    text = (
        payload.get("text")
        if isinstance(payload.get("text"), str)
        else (payload.get("text") or {}).get("message")
        if isinstance(payload.get("text"), dict)
        else None
    )
    text = (
        text
        or message.get("conversation")
        or (message.get("extendedTextMessage") or {}).get("text")
        or payload.get("body")
        or ""
    )
    text = str(text).strip()

    if not phone or not text:
        return None
    return {
        "phone": phone,
        "text": text[:32_000],
        "message_id": str(payload.get("messageId") or payload.get("id") or ""),
    }
