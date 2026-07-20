"""Shared object-storage reader (gs:// or local path)."""

from pathlib import Path


def load_bytes(storage_path: str) -> bytes:
    if storage_path.startswith("gs://"):
        from google.cloud import storage as gcs

        _, _, rest = storage_path.partition("gs://")
        bucket_name, _, blob_name = rest.partition("/")
        return gcs.Client().bucket(bucket_name).blob(blob_name).download_as_bytes()
    return Path(storage_path).read_bytes()
