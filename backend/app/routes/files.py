"""Business file uploads and their ingestion lifecycle.

Upload → storage + files row (pending) → ingestion task (Cloud Tasks in
production, inline background call in dev) → kernel chunks/embeds → ready.
"""

import json
import logging
import uuid

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile

from app.auth import require
from app.config import settings
from app.db import get_connection
from app.storage import save_bytes
from app.tenancy import resolve_target_tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/files", tags=["files"])

ALLOWED_EXTENSIONS = (".pdf", ".docx", ".txt", ".md", ".csv", ".xlsx", ".xlsm")


def _serialize(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "name": row["name"],
        "content_type": row["content_type"],
        "size_bytes": row["size_bytes"],
        "status": row["status"],
        "error_detail": row["error_detail"],
        "chunk_count": row["chunk_count"],
        "created_at": row["created_at"].isoformat(),
    }


def embedding_spec_for_tenant(tenant_id) -> dict:
    from app.template_runtime import _embedding_spec

    with get_connection() as conn:
        return _embedding_spec(conn, tenant_id)


async def _kernel_headers() -> dict:
    if settings.kernel_audience:
        from app.gcp_auth import id_token_for

        return {"Authorization": f"Bearer {await id_token_for(settings.kernel_audience)}"}
    if settings.kernel_internal_token:
        return {"Authorization": f"Bearer {settings.kernel_internal_token}"}
    return {}


async def _ingest_inline(file_id: str, embedding: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            await client.post(
                f"{settings.kernel_url}/v1/ingest-file",
                json={"file_id": file_id, "embedding": embedding},
                headers=await _kernel_headers(),
            )
    except httpx.HTTPError:
        logger.exception("inline ingestion call failed for %s", file_id)


def _enqueue_ingestion(background: BackgroundTasks, file_id: str, embedding: dict) -> None:
    if settings.cloud_tasks_queue:
        from google.cloud import tasks_v2

        client = tasks_v2.CloudTasksClient()
        task = {
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": f"{settings.kernel_url}/v1/ingest-file",
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"file_id": file_id, "embedding": embedding}).encode(),
                "oidc_token": {
                    "service_account_email": settings.tasks_service_account,
                    "audience": settings.kernel_audience or settings.kernel_url,
                },
            }
        }
        client.create_task(parent=settings.cloud_tasks_queue, task=task)
    else:
        background.add_task(_ingest_inline, file_id, embedding)


@router.get("")
def list_files(user: dict = Depends(require("files", "view"))):
    with get_connection() as conn:
        scope = " WHERE NOT is_deleted" if user["is_master"] else " WHERE NOT is_deleted AND tenant_id = %s"
        params = () if user["is_master"] else (user["tenant_id"],)
        rows = conn.execute(
            f"SELECT * FROM files{scope} ORDER BY created_at DESC", params
        ).fetchall()
    return [_serialize(r) for r in rows]


@router.post("", status_code=201)
async def upload_file(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    tenant_id: str | None = Form(default=None),
    user: dict = Depends(require("files", "create")),
):
    name = file.filename or "arquivo"
    if not name.lower().endswith(ALLOWED_EXTENSIONS):
        raise HTTPException(
            status_code=400,
            detail=f"Extensão não suportada. Aceitas: {', '.join(ALLOWED_EXTENSIONS)}",
        )
    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Arquivo grande demais (máx. 50MB)")
    if not data:
        raise HTTPException(status_code=400, detail="Arquivo vazio")

    target_tenant = resolve_target_tenant(user, tenant_id)
    file_id = str(uuid.uuid4())
    content_type = file.content_type or "application/octet-stream"
    storage_path = save_bytes(
        f"tenants/{target_tenant}/files/{file_id}/{name}", data, content_type
    )

    with get_connection() as conn:
        row = conn.execute(
            """INSERT INTO files (id, tenant_id, name, content_type, size_bytes, storage_path)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
            (file_id, target_tenant, name, content_type, len(data), storage_path),
        ).fetchone()

    _enqueue_ingestion(background, file_id, embedding_spec_for_tenant(target_tenant))
    return _serialize(row)


@router.post("/{file_id}/reprocess")
async def reprocess(
    background: BackgroundTasks,
    file_id: str,
    user: dict = Depends(require("files", "edit")),
):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM files WHERE id = %s", (file_id,)).fetchone()
        if row is None or (
            not user["is_master"] and str(row["tenant_id"]) != str(user["tenant_id"])
        ):
            raise HTTPException(status_code=404, detail="Arquivo não encontrado")
        conn.execute(
            "UPDATE files SET status = 'pending', error_detail = NULL WHERE id = %s",
            (file_id,),
        )
    _enqueue_ingestion(background, file_id, embedding_spec_for_tenant(row["tenant_id"]))
    return {"status": "ok"}


@router.delete("/{file_id}")
def archive_file(file_id: str, user: dict = Depends(require("files", "delete"))):
    """Soft-delete: hides the file from listings; template versions that
    already reference it keep working (versions are immutable)."""
    with get_connection() as conn:
        row = conn.execute("SELECT tenant_id FROM files WHERE id = %s", (file_id,)).fetchone()
        if row is None or (
            not user["is_master"] and str(row["tenant_id"]) != str(user["tenant_id"])
        ):
            raise HTTPException(status_code=404, detail="Arquivo não encontrado")
        conn.execute(
            "UPDATE files SET is_deleted = TRUE, updated_at = now() WHERE id = %s",
            (file_id,),
        )
    return {"status": "ok"}
