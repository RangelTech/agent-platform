"""Artifact access for the frontend: metadata, JSON payload (charts) and
file download (signed GCS URL in production, streamed file in dev)."""

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse, Response

from app.auth import current_user
from app.db import get_connection

router = APIRouter(prefix="/api", tags=["artifacts"])

_PAYLOAD_CAP_BYTES = 5 * 1024 * 1024


def _scoped(artifact_id: str, user: dict) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM artifacts WHERE id = %s", (artifact_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Artifact não encontrado")
    if (
        not user["is_master"]
        and row["tenant_id"] is not None
        and str(row["tenant_id"]) != str(user["tenant_id"])
    ):
        raise HTTPException(status_code=404, detail="Artifact não encontrado")
    return row


def _load_bytes(storage_path: str) -> bytes:
    if storage_path.startswith("gs://"):
        from google.cloud import storage as gcs

        _, _, rest = storage_path.partition("gs://")
        bucket_name, _, blob_name = rest.partition("/")
        return gcs.Client().bucket(bucket_name).blob(blob_name).download_as_bytes()
    return Path(storage_path).read_bytes()


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str, user: dict = Depends(current_user)):
    row = _scoped(artifact_id, user)
    return {
        "id": str(row["id"]),
        "kind": row["kind"],
        "title": row["title"],
        "schema": row["schema_json"],
        "preview": row["preview_json"],
        "row_count": row["row_count"],
        "content_type": row["content_type"],
        "created_at": row["created_at"].isoformat(),
    }


@router.get("/artifacts/{artifact_id}/payload")
def get_payload(artifact_id: str, user: dict = Depends(current_user)):
    """JSON payload for renderable artifacts (charts, datasets)."""
    row = _scoped(artifact_id, user)
    if row["content_type"] != "application/json":
        raise HTTPException(status_code=400, detail="Artifact não é JSON — use /download")
    data = _load_bytes(row["storage_path"])
    if len(data) > _PAYLOAD_CAP_BYTES:
        raise HTTPException(status_code=413, detail="Payload grande demais — use /download")
    return json.loads(data)


@router.get("/artifacts/{artifact_id}/download")
def download(artifact_id: str, user: dict = Depends(current_user)):
    row = _scoped(artifact_id, user)
    if row["storage_path"].startswith("gs://"):
        from datetime import timedelta

        from google.cloud import storage as gcs

        _, _, rest = row["storage_path"].partition("gs://")
        bucket_name, _, blob_name = rest.partition("/")
        try:
            url = (
                gcs.Client()
                .bucket(bucket_name)
                .blob(blob_name)
                .generate_signed_url(expiration=timedelta(hours=1))
            )
            return RedirectResponse(url)
        except Exception:  # noqa: BLE001 — sign can fail without SA key; stream instead
            data = _load_bytes(row["storage_path"])
    else:
        data = _load_bytes(row["storage_path"])
    filename = row["title"].replace('"', "") or "arquivo"
    return Response(
        content=data,
        media_type=row["content_type"],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/chats/{chat_id}/artifacts")
def list_chat_artifacts(chat_id: str, user: dict = Depends(current_user)):
    """Artifacts produced in a conversation, for re-rendering after reload."""
    with get_connection() as conn:
        chat = conn.execute(
            "SELECT id FROM chats WHERE id = %s AND user_id = %s",
            (chat_id, user["id"]),
        ).fetchone()
        if chat is None:
            raise HTTPException(status_code=404, detail="Conversa não encontrada")
        rows = conn.execute(
            """SELECT id, kind, title, row_count, created_at
                 FROM artifacts WHERE chat_id = %s ORDER BY created_at""",
            (chat_id,),
        ).fetchall()
    return [
        {
            "artifact_id": str(r["id"]),
            "kind": r["kind"],
            "title": r["title"],
            "row_count": r["row_count"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]
