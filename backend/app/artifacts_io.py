"""Shared object-storage helpers for backend readers/download routes."""

from pathlib import Path
from urllib.parse import quote

from app.config import settings

_gcs_client = None
_s3_client = None


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


def _split_storage_uri(storage_path: str, scheme: str) -> tuple[str, str]:
    _, _, rest = storage_path.partition(f"{scheme}://")
    bucket_name, _, object_key = rest.partition("/")
    return bucket_name, object_key


def load_bytes(storage_path: str) -> bytes:
    if storage_path.startswith("s3://"):
        bucket_name, object_key = _split_storage_uri(storage_path, "s3")
        response = _s3().get_object(Bucket=bucket_name, Key=object_key)
        return response["Body"].read()
    if storage_path.startswith("gs://"):
        bucket_name, object_key = _split_storage_uri(storage_path, "gs")
        return _gcs().bucket(bucket_name).blob(object_key).download_as_bytes()
    return Path(storage_path).read_bytes()


def signed_download_url(storage_path: str, expires_seconds: int | None = None) -> str | None:
    expires = expires_seconds or settings.s3_presign_expires_seconds
    if storage_path.startswith("s3://"):
        bucket_name, object_key = _split_storage_uri(storage_path, "s3")
        public_base = (settings.s3_public_base_url or "").rstrip("/")
        if public_base:
            return f"{public_base}/{quote(object_key)}"
        return _s3().generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket_name, "Key": object_key},
            ExpiresIn=expires,
        )
    if storage_path.startswith("gs://"):
        from datetime import timedelta

        bucket_name, object_key = _split_storage_uri(storage_path, "gs")
        return (
            _gcs()
            .bucket(bucket_name)
            .blob(object_key)
            .generate_signed_url(expiration=timedelta(seconds=expires))
        )
    return None
