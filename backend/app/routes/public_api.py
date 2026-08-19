"""Public machine-to-machine API: POST /v1/messages.

Auth: Bearer API key from an active integration. Modes:
  sync    → JSON reply in the response body (default)
  stream  → SSE passthrough from the kernel
  webhook → 202 now; the reply is POSTed to the integration's webhook_url
            signed with HMAC-SHA256 (X-Agent-Signature: sha256=<hex>).

Conversation continuity: external_session_id maps to a stable kernel thread.
"""

import base64
import hashlib
import hmac
import json
import logging

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.crypto import decrypt
from app.db import get_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["public"])


class PublicAttachmentIn(BaseModel):
    """An attachment sent inline as base64 — used today by the Chatwoot
    bridge to forward a voice note it already downloaded from Chatwoot.

    Base64-in-JSON (not multipart, not "here's a URL, go fetch it yourself")
    on purpose: the public API is JSON-only end to end (sync/stream/webhook
    all share the same body shape), voice notes are small enough that base64
    inline is cheap, and the bridge already has the bytes in hand after
    downloading from Chatwoot — asking the backend to fetch an arbitrary
    Chatwoot `data_url` back would just add a second, less-trusted HTTP hop
    (auth/reachability of that URL is Chatwoot-deployment-specific) for no
    benefit.
    """

    kind: str = Field(default="audio", pattern="^(audio|image|file)$")
    name: str = Field(default="anexo", max_length=200)
    content_type: str = Field(default="application/octet-stream", max_length=100)
    data_base64: str = Field(min_length=1)


class PublicMessageIn(BaseModel):
    # Optional because an audio-only message (a voice note with no caption)
    # has nothing to put here; `_build_kernel_payload` fills in a placeholder
    # when there are attachments and no text.
    message: str = Field(default="", max_length=32_000)
    external_session_id: str = Field(default="default", max_length=200)
    mode: str = Field(default="sync", pattern="^(sync|stream|webhook)$")
    # Overrides the integration's default template for this call.
    template_id: str | None = None
    attachments: list[PublicAttachmentIn] = Field(default_factory=list)


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


def _store_public_attachments(
    integration: dict, payload: PublicMessageIn
) -> tuple[list[dict], dict]:
    """Decode+persist the inline base64 attachments, resolving a transcription
    spec when any of them is audio. Reuses `app.attachments` — the exact same
    storage + Whisper-provider-lookup code the frontend chat path already
    uses (`routes/chats.py`) — so this is one pipeline, not two."""
    if not payload.attachments:
        return [], {}

    from app.attachments import store_bytes, transcription_spec

    stored: list[dict] = []
    has_audio = False
    for attachment in payload.attachments:
        try:
            data = base64.b64decode(attachment.data_base64, validate=True)
        except Exception as exc:  # noqa: BLE001 — bad input is a 400, not a 500
            raise HTTPException(
                status_code=400, detail=f"anexo '{attachment.name}': base64 inválido"
            ) from exc
        descriptor = store_bytes(
            integration["tenant_id"],
            name=attachment.name,
            content_type=attachment.content_type,
            data=data,
            kind=attachment.kind,
        )
        stored.append(descriptor)
        has_audio = has_audio or descriptor["kind"] == "audio"

    transcription = transcription_spec(integration["tenant_id"]) if has_audio else {}
    return stored, transcription


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

    stored_attachments, transcription = _store_public_attachments(integration, payload)

    # A voice-note-only message ("olá" never gets typed) has nothing in
    # `payload.message` — the real content only exists once the kernel
    # transcribes the attachment. A caption-less text message is still a
    # client error, same as before this endpoint accepted attachments.
    message = payload.message.strip()
    if not message:
        if stored_attachments:
            message = "(áudio recebido)"
        else:
            raise HTTPException(status_code=422, detail="message é obrigatório")

    run = build_run_payload(integration["tenant_id"], template_id)
    thread_id = f"ext-{integration['id']}-{payload.external_session_id}"
    kernel_payload = {"thread_id": thread_id, "message": message, **run}
    if stored_attachments:
        kernel_payload["attachments"] = stored_attachments
        kernel_payload["transcription"] = transcription
    return kernel_payload


async def _run_to_completion(kernel_payload: dict) -> tuple[str, list[dict]]:
    """Call the kernel and return (final reply text, artifacts) or raise.

    Artifacts (charts, PIX QR images, files) ride the same SSE stream as
    `artifact` events — before this, `/v1/messages` (sync/webhook modes) threw
    them away and only kept the `done` text. That is the reason artifacts
    like the PIX QR code never reached channels that go through this
    machine-to-machine API (Chatwoot/WhatsApp via the bridge), while they
    worked fine on the web chat (which talks to the kernel's own SSE stream
    directly, not through here).
    """
    reply = ""
    artifacts: list[dict] = []
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
                    elif current_event == "artifact":
                        data = json.loads(line[6:])
                        artifacts.append(
                            {
                                "artifact_id": data["artifact_id"],
                                "kind": data["kind"],
                                "title": data["title"],
                            }
                        )
                    elif current_event == "error":
                        detail = json.loads(line[6:]).get("detail", "erro")
                        raise HTTPException(status_code=502, detail=detail)
    return reply, artifacts


async def _deliver_webhook(integration: dict, session_id: str, kernel_payload: dict) -> None:
    artifacts: list[dict] = []
    try:
        reply, artifacts = await _run_to_completion(kernel_payload)
    except HTTPException as exc:
        reply = f"ERRO: {exc.detail}"
    _log_message(integration["id"], session_id, "out", reply)

    body = json.dumps(
        {"external_session_id": session_id, "reply": reply, "artifacts": artifacts},
        ensure_ascii=False,
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
async def public_message(
    request: Request, payload: PublicMessageIn, background: BackgroundTasks
):
    integration = _authenticate(request)
    _enforce_rate_limit(integration)
    _log_message(
        integration["id"], payload.external_session_id, "in", payload.message
    )
    kernel_payload = _build_kernel_payload(integration, payload)

    if payload.mode == "webhook":
        if not integration["webhook_url"]:
            raise HTTPException(status_code=400, detail="Integração sem webhook_url")
        # BackgroundTasks (e não create_task solto): a entrega roda depois da
        # resposta, mas ainda dentro do ciclo de vida da requisição — uma task
        # órfã morre junto com o loop quando o servidor recicla o worker.
        background.add_task(
            _deliver_webhook, dict(integration), payload.external_session_id, kernel_payload
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

    reply, artifacts = await _run_to_completion(kernel_payload)
    _log_message(integration["id"], payload.external_session_id, "out", reply)
    return {
        "external_session_id": payload.external_session_id,
        "reply": reply,
        "artifacts": artifacts,
    }


@router.get("/artifacts/{artifact_id}")
async def get_artifact_for_integration(artifact_id: str, request: Request):
    """Lets a caller of `/v1/messages` (the Chatwoot bridge, or any other
    machine-to-machine integration) fetch the bytes of an artifact its turn
    produced — same Bearer integration key used for `/v1/messages`, no user
    session required (`/api/artifacts/*` needs a logged-in user, which a
    server-to-server bridge doesn't have).

    Scoped by tenant: an integration can only ever fetch artifacts that
    belong to its own tenant, same isolation rule as `/api/artifacts/*`.
    """
    integration = _authenticate(request)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM artifacts WHERE id = %s", (artifact_id,)
        ).fetchone()
    if row is None or (
        row["tenant_id"] is not None
        and str(row["tenant_id"]) != str(integration["tenant_id"])
    ):
        raise HTTPException(status_code=404, detail="Artifact não encontrado")

    from fastapi.responses import Response

    from app.artifacts_io import load_bytes

    data = load_bytes(row["storage_path"])
    filename = (row["title"] or "arquivo").replace('"', "")
    return Response(
        content=data,
        media_type=row["content_type"],
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "X-Artifact-Kind": row["kind"],
        },
    )
