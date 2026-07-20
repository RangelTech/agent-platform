"""Public machine-to-machine API: POST /v1/messages.

Auth: Bearer API key from an active integration. Modes:
  sync    → JSON reply in the response body (default)
  stream  → SSE passthrough from the kernel
  webhook → 202 now; the reply is POSTed to the integration's webhook_url
            signed with HMAC-SHA256 (X-Agent-Signature: sha256=<hex>).

Conversation continuity: external_session_id maps to a stable kernel thread.
"""

import asyncio
import hashlib
import hmac
import json
import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.crypto import decrypt
from app.db import get_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["public"])


class PublicMessageIn(BaseModel):
    message: str = Field(min_length=1, max_length=32_000)
    external_session_id: str = Field(default="default", max_length=200)
    mode: str = Field(default="sync", pattern="^(sync|stream|webhook)$")
    # Overrides the integration's default template for this call.
    template_id: str | None = None


def _authenticate(request: Request) -> dict:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="API key ausente")
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    with get_connection() as conn:
        row = conn.execute(
            """SELECT i.*, t.is_active AS tenant_active
                 FROM integrations i JOIN tenants t ON t.id = i.tenant_id
                WHERE i.api_key_hash = %s""",
            (key_hash,),
        ).fetchone()
    if (
        row is None
        or not row["is_active"]
        or row["revoked_at"] is not None
        or not row["tenant_active"]
    ):
        raise HTTPException(status_code=401, detail="API key inválida ou revogada")
    return row


def _enforce_rate_limit(integration: dict) -> None:
    with get_connection() as conn:
        count = conn.execute(
            """SELECT count(*) AS n FROM integration_messages
                WHERE integration_id = %s AND direction = 'in'
                  AND created_at > now() - interval '1 minute'""",
            (integration["id"],),
        ).fetchone()["n"]
    if count >= integration["rate_limit_per_minute"]:
        raise HTTPException(status_code=429, detail="Limite de requisições excedido")


def _log_message(integration_id, session_id: str, direction: str, content: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO integration_messages
                   (integration_id, external_session_id, direction, content)
               VALUES (%s, %s, %s, %s)""",
            (integration_id, session_id, direction, content[:10_000]),
        )


async def _kernel_headers() -> dict:
    if settings.kernel_audience:
        from app.gcp_auth import id_token_for

        return {"Authorization": f"Bearer {await id_token_for(settings.kernel_audience)}"}
    if settings.kernel_internal_token:
        return {"Authorization": f"Bearer {settings.kernel_internal_token}"}
    return {}


def _build_kernel_payload(integration: dict, payload: PublicMessageIn) -> dict:
    from app.template_runtime import build_run_payload

    template_id = payload.template_id or (
        str(integration["template_id"]) if integration["template_id"] else None
    )
    if payload.template_id:
        with get_connection() as conn:
            owned = conn.execute(
                "SELECT 1 FROM templates WHERE id = %s AND tenant_id = %s AND NOT is_deleted",
                (payload.template_id, integration["tenant_id"]),
            ).fetchone()
        if owned is None:
            raise HTTPException(status_code=404, detail="Template não encontrado")

    run = build_run_payload(integration["tenant_id"], template_id)
    thread_id = f"ext-{integration['id']}-{payload.external_session_id}"
    return {"thread_id": thread_id, "message": payload.message, **run}


async def _run_to_completion(kernel_payload: dict) -> str:
    """Call the kernel and return the final reply text (or raise)."""
    reply = ""
    async with httpx.AsyncClient(timeout=httpx.Timeout(310.0)) as client:
        async with client.stream(
            "POST",
            f"{settings.kernel_url}/v1/runs",
            json=kernel_payload,
            headers=await _kernel_headers(),
        ) as response:
            if response.status_code != 200:
                detail = (await response.aread()).decode()[:300]
                raise HTTPException(status_code=502, detail=f"kernel: {detail}")
            current_event = ""
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    current_event = line[7:].strip()
                elif line.startswith("data: "):
                    if current_event == "done":
                        reply = json.loads(line[6:]).get("text", "")
                    elif current_event == "error":
                        detail = json.loads(line[6:]).get("detail", "erro")
                        raise HTTPException(status_code=502, detail=detail)
    return reply


async def _deliver_webhook(integration: dict, session_id: str, kernel_payload: dict) -> None:
    try:
        reply = await _run_to_completion(kernel_payload)
    except HTTPException as exc:
        reply = f"ERRO: {exc.detail}"
    _log_message(integration["id"], session_id, "out", reply)

    body = json.dumps(
        {"external_session_id": session_id, "reply": reply}, ensure_ascii=False
    ).encode()
    secret = (
        decrypt(integration["webhook_secret_encrypted"])
        if integration["webhook_secret_encrypted"]
        else ""
    )
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.post(
                integration["webhook_url"],
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Agent-Signature": f"sha256={signature}",
                },
            )
    except httpx.HTTPError:
        logger.exception("webhook delivery failed for %s", integration["id"])


@router.post("/messages")
async def public_message(request: Request, payload: PublicMessageIn):
    integration = _authenticate(request)
    _enforce_rate_limit(integration)
    _log_message(
        integration["id"], payload.external_session_id, "in", payload.message
    )
    kernel_payload = _build_kernel_payload(integration, payload)

    if payload.mode == "webhook":
        if not integration["webhook_url"]:
            raise HTTPException(status_code=400, detail="Integração sem webhook_url")
        asyncio.get_running_loop().create_task(
            _deliver_webhook(dict(integration), payload.external_session_id, kernel_payload)
        )
        return {"status": "accepted"}

    if payload.mode == "stream":
        async def passthrough():
            async with httpx.AsyncClient(timeout=httpx.Timeout(310.0)) as client:
                async with client.stream(
                    "POST",
                    f"{settings.kernel_url}/v1/runs",
                    json=kernel_payload,
                    headers=await _kernel_headers(),
                ) as response:
                    async for line in response.aiter_lines():
                        yield line + "\n"

        return StreamingResponse(passthrough(), media_type="text/event-stream")

    reply = await _run_to_completion(kernel_payload)
    _log_message(integration["id"], payload.external_session_id, "out", reply)
    return {"external_session_id": payload.external_session_id, "reply": reply}
