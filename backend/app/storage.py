"""Object storage for the backend (uploads): GCS in production, local dir in
dev — same layout the kernel uses."""

from pathlib import Path

from app.config import settings

_gcs_client = None


def _gcs():
    global _gcs_client
    if _gcs_client is None:
        from google.cloud import storage as gcs

        _gcs_client = gcs.Client()
    return _gcs_client


def save_bytes(relative_path: str, data: bytes, content_type: str) -> str:
    if settings.gcs_bucket:
        blob_name = f"{settings.gcs_prefix.rstrip('/')}/{relative_path}"
        _gcs().bucket(settings.gcs_bucket).blob(blob_name).upload_from_string(
            data, content_type=content_type
        )
        return f"gs://{settings.gcs_bucket}/{blob_name}"
    base = Path(settings.storage_local_dir)
    path = base / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)
