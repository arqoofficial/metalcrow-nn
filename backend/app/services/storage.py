"""MinIO client — хранилище исходников (PDF/DOC/CSV) для provenance (SPEC_V3 §5.5/§8.8)."""

from datetime import timedelta
from io import BytesIO
from typing import Any

from minio import Minio
from minio.error import S3Error

from app.core.config import settings

_client: Minio | None = None
_public_client: Minio | None = None


def _build_minio_client(endpoint: str) -> Minio:
    return Minio(
        endpoint,
        access_key=settings.MINIO_ROOT_USER,
        secret_key=settings.MINIO_ROOT_PASSWORD,
        secure=settings.MINIO_SECURE,
    )


def get_minio_client() -> Minio:
    global _client
    if _client is None:
        _client = _build_minio_client(settings.MINIO_ENDPOINT)
    return _client


def get_minio_public_client() -> Minio:
    """Client for presigned URLs — must use a host the browser can reach."""
    global _public_client
    if _public_client is None:
        endpoint = settings.MINIO_PUBLIC_ENDPOINT or settings.MINIO_ENDPOINT
        _public_client = _build_minio_client(endpoint)
    return _public_client


def upload_document(*, minio_key: str, data: bytes, content_type: str) -> None:
    """Бакет создаётся один раз при старте compose (сервис `minio-init`)."""
    get_minio_client().put_object(
        settings.MINIO_BUCKET,
        minio_key,
        BytesIO(data),
        length=len(data),
        content_type=content_type,
    )


class StorageObjectNotFoundError(FileNotFoundError):
    """Raised when a document record points to a missing MinIO object."""


def open_document(*, minio_key: str) -> Any:
    try:
        return get_minio_client().get_object(settings.MINIO_BUCKET, minio_key)
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            raise StorageObjectNotFoundError(minio_key) from exc
        raise


def presigned_download_url(*, minio_key: str, expires_minutes: int = 15) -> str:
    return get_minio_public_client().presigned_get_object(
        settings.MINIO_BUCKET,
        minio_key,
        expires=timedelta(minutes=expires_minutes),
    )


def delete_document(*, minio_key: str) -> None:
    get_minio_client().remove_object(settings.MINIO_BUCKET, minio_key)
