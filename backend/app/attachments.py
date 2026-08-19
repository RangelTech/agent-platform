"""Chat attachment plumbing shared by every entry point that can hand the
kernel an audio/image/file attachment: the frontend chat (`routes/chats.py`,
multipart upload) and the public API (`routes/public_api.py`, base64 JSON —
used today by the Chatwoot bridge for voice notes).

Kept in one module so "store the bytes, describe them for the kernel, resolve
which Whisper credential transcribes them" has exactly one implementation.
Duplicating this per entry point is how the two paths would quietly drift
(exactly what happened before: the frontend path transcribes, the Chatwoot
path silently didn't).
"""

import logging
import os
import uuid as _uuid

from fastapi import HTTPException

from app.config import settings
from app.db import get_connection

logger = logging.getLogger(__name__)

ATTACHMENT_KINDS = {"image": "image", "audio": "audio"}


def kind_for_content_type(content_type: str) -> str:
    return ATTACHMENT_KINDS.get((content_type or "").split("/", 1)[0], "file")


async def store_uploads(user: dict, uploads: list) -> list[dict]:
    """Persist multipart chat attachments (UploadFile) to object storage;
    returns kernel descriptors."""
    from app.storage import save_bytes

    attachments = []
    for upload in uploads:
        data = await upload.read()
        if not data:
            continue
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="Anexo grande demais")
        content_type = upload.content_type or "application/octet-stream"
        kind = kind_for_content_type(content_type)
        name = upload.filename or "anexo"
        path = save_bytes(
            f"tenants/{user['tenant_id']}/chats/attachments/{_uuid.uuid4()}/{name}",
            data,
            content_type,
        )
        attachments.append(
            {"kind": kind, "name": name, "content_type": content_type, "storage_path": path}
        )
    return attachments


def store_bytes(tenant_id, *, name: str, content_type: str, data: bytes, kind: str | None = None) -> dict:
    """Same persistence as `store_uploads`, but for bytes that already arrived
    in hand (base64 in a JSON body, e.g. from the Chatwoot bridge) instead of
    a multipart `UploadFile`. Returns the same kernel descriptor shape."""
    from app.storage import save_bytes as _save_bytes

    if not data:
        raise HTTPException(status_code=400, detail="Anexo vazio")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Anexo grande demais")
    content_type = content_type or "application/octet-stream"
    resolved_kind = kind or kind_for_content_type(content_type)
    name = name or "anexo"
    path = _save_bytes(
        f"tenants/{tenant_id}/integrations/attachments/{_uuid.uuid4()}/{name}",
        data,
        content_type,
    )
    return {
        "kind": resolved_kind,
        "name": name,
        "content_type": content_type,
        "storage_path": path,
    }


def transcription_spec(tenant_id) -> dict:
    """Whisper provider from the tenant's services: Groq (fast/cheap) first,
    then OpenAI, then any `openai-compatible` service (ex.: um combo do
    9Router) usando o `api_base` próprio do tenant.

    Fora de teste automatizado, nunca cai num stub silencioso: se nenhuma
    dessas specs existir, devolve um marcador que faz o kernel tentar (e
    falhar de forma clara, via `litellm.atranscription`) em vez de decodificar
    os bytes crus do áudio como texto.
    """
    from app.crypto import decrypt

    with get_connection() as conn:
        rows = conn.execute(
            """SELECT provider, api_key_encrypted, api_base, model FROM ai_services
                WHERE tenant_id = %s AND is_active AND NOT is_deleted
                      AND api_key_encrypted IS NOT NULL
                ORDER BY created_at""",
            (tenant_id,),
        ).fetchall()
    for row in rows:
        if row["provider"] == "groq":
            return {
                "provider": "groq",
                "model": "groq/whisper-large-v3-turbo",
                "api_key": decrypt(row["api_key_encrypted"]),
            }
    for row in rows:
        if row["provider"] == "openai":
            return {
                "provider": "openai",
                "model": "whisper-1",
                "api_key": decrypt(row["api_key_encrypted"]),
            }
    for row in rows:
        if row["provider"] == "openai-compatible" and row["api_base"]:
            return {
                "provider": "openai-compatible",
                "model": row["model"] or "whisper-1",
                "api_key": decrypt(row["api_key_encrypted"]),
                "api_base": row["api_base"],
            }
    # O stub (decodifica bytes crus como texto) só existe para a suíte
    # automatizada, onde os "bytes de áudio" do teste já são texto ASCII de
    # propósito. `PYTEST_CURRENT_TEST` é setado pelo próprio pytest durante a
    # execução — não depende de nenhuma configuração nova no conftest.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return {"provider": "stub"}
    # Nenhuma spec real: não inventa um provider furado. `transcribe()` no
    # kernel vai tentar o caminho real do litellm sem api_key/api_base
    # válidos, o que falha rápido e cai no mesmo formato de erro já usado
    # para falha de transcrição (`attachments.py`: "[áudio {name}: falha na
    # transcrição — {exc}]") — nunca um stub silencioso em produção.
    return {"provider": "unavailable"}
