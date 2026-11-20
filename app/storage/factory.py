"""Construct the configured storage adapter."""

from app.config import Settings
from app.storage.base import ArtifactStorage
from app.storage.local import LocalStorage
from app.storage.s3 import S3Storage


def build_storage(settings: Settings) -> ArtifactStorage:
    """Return local disk or S3-compatible storage according to configuration."""
    if settings.storage_backend.lower() == "s3":
        return S3Storage(
            endpoint_url=settings.s3_endpoint_url,
            access_key=settings.s3_access_key_id,
            secret_key=settings.s3_secret_access_key.get_secret_value(),
            bucket=settings.s3_bucket,
            region=settings.s3_region,
        )
    if settings.storage_backend.lower() != "local":
        raise ValueError("STORAGE_BACKEND must be 'local' or 's3'")
    return LocalStorage(settings.local_storage_dir)
