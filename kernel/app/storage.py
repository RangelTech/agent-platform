"""Artifact payload storage: GCS in production, local filesystem in dev.

Metadata lives in the artifacts table (written here too, same pattern as the
tool-call trace); only the full payload goes to object storage.
"""

import json
import logging
import uuid
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

_gcs_client = None


def _gcs():
    global _gcs_client
    if _gcs_client is None:
        from google.cloud import storage as gcs

        _gcs_client = gcs.Client()
    return _gcs_client


def save_payload(relative_path: str, data: bytes, content_type: str) -> str:
    """Store bytes; returns the storage path (gs://... or local file path)."""
    if settings.gcs_bucket:
        blob_name = f"{settings.gcs_prefix.rstrip('/')}/{relative_path}"
        bucket = _gcs().bucket(settings.gcs_bucket)
        bucket.blob(blob_name).upload_from_string(data, content_type=content_type)
        return f"gs://{settings.gcs_bucket}/{blob_name}"
    base = Path(settings.artifacts_local_dir)
    path = base / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def load_payload(storage_path: str) -> bytes:
    if storage_path.startswith("gs://"):
        _, _, rest = storage_path.partition("gs://")
        bucket_name, _, blob_name = rest.partition("/")
        return _gcs().bucket(bucket_name).blob(blob_name).download_as_bytes()
    return Path(storage_path).read_bytes()


def signed_url(storage_path: str, expires_seconds: int = 3600) -> str | None:
    """Browser-usable URL for a stored payload (None for local dev paths)."""
    if not storage_path.startswith("gs://"):
        return None
    from datetime import timedelta

    _, _, rest = storage_path.partition("gs://")
    bucket_name, _, blob_name = rest.partition("/")
    try:
        return (
            _gcs()
            .bucket(bucket_name)
            .blob(blob_name)
            .generate_signed_url(expiration=timedelta(seconds=expires_seconds))
        )
    except Exception:  # noqa: BLE001 — SA without signing perms: degrade to no URL
        logger.exception("failed to sign url for %s", storage_path)
        return None


async def register_artifact(
    *,
    tenant_id,
    chat_id,
    agent_name: str,
    kind: str,
    title: str,
    schema_json: dict | list | None,
    preview_json: dict | list | None,
    row_count: int | None,
    payload: bytes,
    content_type: str = "application/json",
    extension: str = "json",
) -> dict:
    """Persist payload + metadata row. Returns the artifact descriptor the
    tools hand back to the model (id + schema + preview, never the payload)."""
    artifact_id = str(uuid.uuid4())
    tenant_segment = str(tenant_id) if tenant_id else "shared"
    relative = f"tenants/{tenant_segment}/artifacts/{kind}/{artifact_id}.{extension}"
    storage_path = save_payload(relative, payload, content_type)

    from app.trace import _get_pool  # same tiny pool, artifacts are trace-adjacent

    pool = await _get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO artifacts
                   (id, tenant_id, chat_id, agent_name, kind, title, schema_json,
                    preview_json, row_count, storage_path, content_type)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                artifact_id,
                tenant_id,
                chat_id,
                agent_name,
                kind,
                title,
                json.dumps(schema_json, ensure_ascii=False, default=str),
                json.dumps(preview_json, ensure_ascii=False, default=str),
                row_count,
                storage_path,
                content_type,
            ),
        )
    return {
        "artifact_id": artifact_id,
        "kind": kind,
        "title": title,
        "schema": schema_json,
        "preview": preview_json,
        "row_count": row_count,
    }


async def get_artifact(artifact_id: str) -> dict | None:
    from app.trace import _get_pool

    pool = await _get_pool()
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT * FROM artifacts WHERE id = %s", (artifact_id,)
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    columns = [d.name for d in cursor.description]
    return dict(zip(columns, row, strict=True))
