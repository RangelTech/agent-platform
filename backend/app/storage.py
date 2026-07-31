"""Object storage for the backend (uploads): S3-compatible/MinIO, GCS, or
local dir in dev — same layout the kernel uses."""

from pathlib import Path

from app.config import settings

_gcs_client = None
_s3_client = None


def _storage_backend() -> str:
    forced = (settings.storage_backend or "").strip().lower()
    if forced:
        return forced
    if settings.s3_bucket:
        return "s3"
    if settings.gcs_bucket:
        return "gcs"
    return "local"


def _s3():
    global _s3_client
    if _s3_client is None:
        import boto3

        kwargs = {
            "service_name": "s3",
            "region_name": settings.s3_region,
            "aws_access_key_id": settings.s3_access_key_id,
            "aws_secret_access_key": settings.s3_secret_access_key,
        }
        if settings.s3_endpoint_url:
            kwargs["endpoint_url"] = settings.s3_endpoint_url
        _s3_client = boto3.client(**kwargs)
    return _s3_client


def _gcs():
    global _gcs_client
    if _gcs_client is None:
        from google.cloud import storage as gcs

        _gcs_client = gcs.Client()
    return _gcs_client


def _normalize_prefix(prefix: str) -> str:
    return prefix.strip().strip("/")


def _join_key(prefix: str, relative_path: str) -> str:
    clean_prefix = _normalize_prefix(prefix)
    clean_relative = relative_path.lstrip("/")
    return f"{clean_prefix}/{clean_relative}" if clean_prefix else clean_relative


def save_bytes(relative_path: str, data: bytes, content_type: str) -> str:
    backend = _storage_backend()
    if backend == "s3":
        object_key = _join_key(settings.s3_prefix, relative_path)
        _s3().put_object(
            Bucket=settings.s3_bucket,
            Key=object_key,
            Body=data,
            ContentType=content_type,
        )
        return f"s3://{settings.s3_bucket}/{object_key}"
    if backend == "gcs":
        blob_name = _join_key(settings.gcs_prefix, relative_path)
        _gcs().bucket(settings.gcs_bucket).blob(blob_name).upload_from_string(
            data, content_type=content_type
        )
        return f"gs://{settings.gcs_bucket}/{blob_name}"
    base = Path(settings.storage_local_dir)
    path = base / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)
