"""BronzeWriter — writes validated responses to MinIO (S3-compatible) storage."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import structlog
from minio import Minio
from minio.error import S3Error

from geo_bronze.config import Settings
from geo_bronze.errors import BronzeWriteError, StreamingError
from geo_bronze.models.sidecar import BronzeSidecar

logger = structlog.get_logger(__name__)


@dataclass
class WriteResult:
    """Result of a write operation."""

    key: str
    etag: str
    size_bytes: int


class BronzeWriter:
    """Writes data to MinIO bronze bucket, supporting normal and streaming uploads."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = Minio(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        self._bucket = settings.minio_bucket
        self._log = logger.bind(component="BronzeWriter", bucket=self._bucket)

    def ensure_bucket(self) -> None:
        """Create the bronze bucket if it does not exist."""
        try:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
                self._log.info("bucket_created", bucket=self._bucket)
            else:
                self._log.debug("bucket_exists", bucket=self._bucket)
        except S3Error as exc:
            raise BronzeWriteError(f"Failed to ensure bucket '{self._bucket}': {exc}") from exc

    def write(self, key: str, data: bytes, content_type: str) -> WriteResult:
        """Write bytes to bronze storage.

        Args:
            key: Object key in the bucket.
            data: Raw bytes to store.
            content_type: MIME type of the data.

        Returns:
            WriteResult with key, etag, and size.
        """
        import io

        self._log.info("write_start", key=key, size=len(data), content_type=content_type)
        try:
            result = self._client.put_object(
                bucket_name=self._bucket,
                object_name=key,
                data=io.BytesIO(data),
                length=len(data),
                content_type=content_type,
            )
            self._log.info("write_complete", key=key, etag=result.etag)
            return WriteResult(key=key, etag=result.etag or "", size_bytes=len(data))
        except S3Error as exc:
            raise BronzeWriteError(f"Failed to write '{key}': {exc}") from exc

    def write_sidecar(self, key: str, sidecar: BronzeSidecar) -> None:
        """Serialize sidecar to JSON and write it as a separate object.

        Args:
            key: Object key for the sidecar (should end in .sidecar.json).
            sidecar: The BronzeSidecar model to persist.
        """
        import io

        payload = sidecar.model_dump_json(indent=2).encode("utf-8")
        self._log.info("write_sidecar", key=key, size=len(payload))
        try:
            self._client.put_object(
                bucket_name=self._bucket,
                object_name=key,
                data=io.BytesIO(payload),
                length=len(payload),
                content_type="application/json",
            )
        except S3Error as exc:
            raise BronzeWriteError(f"Failed to write sidecar '{key}': {exc}") from exc

    def read_range(self, key: str, offset: int = 0, length: int = 512) -> bytes:
        """Read a byte range from an existing object (used by ValidationAgent).

        Args:
            key: Object key.
            offset: Start byte.
            length: Number of bytes to read.

        Returns:
            Bytes read.
        """
        try:
            response = self._client.get_object(
                bucket_name=self._bucket,
                object_name=key,
                offset=offset,
                length=length,
            )
            data = response.read()
            response.close()
            response.release_conn()
            return data
        except S3Error as exc:
            raise BronzeWriteError(f"Failed to range-read '{key}': {exc}") from exc

    # -------------------------------------------------------------------------
    # Streaming multipart upload
    # -------------------------------------------------------------------------

    def stream_start(self, key: str, content_type: str) -> str:
        """Initiate a multipart upload.

        Args:
            key: Object key for the final object.
            content_type: MIME type.

        Returns:
            upload_id from MinIO.
        """
        self._log.info("stream_start", key=key)
        try:
            upload_id = self._client._create_multipart_upload(  # type: ignore[attr-defined]
                bucket_name=self._bucket,
                object_name=key,
                headers={"Content-Type": content_type},
            )
            return upload_id
        except (S3Error, AttributeError) as exc:
            raise StreamingError(f"Failed to start multipart upload for '{key}': {exc}") from exc

    def stream_part(self, upload_id: str, key: str, part_num: int, chunk: bytes) -> str:
        """Upload one part of a multipart upload.

        Args:
            upload_id: The multipart upload ID.
            key: Object key (same as used in stream_start).
            part_num: 1-based part number.
            chunk: Bytes for this part (must be >= 5 MB except for last part).

        Returns:
            etag of the uploaded part.
        """
        import io

        self._log.debug("stream_part", key=key, part_num=part_num, size=len(chunk))
        try:
            etag = self._client._upload_part(  # type: ignore[attr-defined]
                bucket_name=self._bucket,
                object_name=key,
                upload_id=upload_id,
                part_number=part_num,
                data=io.BytesIO(chunk),
                headers={},
            )
            return etag
        except (S3Error, AttributeError) as exc:
            raise StreamingError(f"Failed to upload part {part_num}: {exc}") from exc

    def stream_complete(self, upload_id: str, key: str, parts: list[tuple[int, str]]) -> None:
        """Complete a multipart upload.

        Args:
            upload_id: The multipart upload ID.
            key: Object key.
            parts: List of (part_number, etag) tuples.
        """
        from minio.commonconfig import ENABLED
        from minio.datatypes import Object

        self._log.info("stream_complete", key=key, parts=len(parts))
        try:
            self._client._complete_multipart_upload(  # type: ignore[attr-defined]
                bucket_name=self._bucket,
                object_name=key,
                upload_id=upload_id,
                parts=parts,
            )
        except (S3Error, AttributeError) as exc:
            raise StreamingError(f"Failed to complete multipart upload '{key}': {exc}") from exc

    def stream_abort(self, upload_id: str, key: str) -> None:
        """Abort an in-progress multipart upload and remove uploaded parts.

        Args:
            upload_id: The multipart upload ID.
            key: Object key.
        """
        self._log.warning("stream_abort", key=key, upload_id=upload_id)
        try:
            self._client._abort_multipart_upload(  # type: ignore[attr-defined]
                bucket_name=self._bucket,
                object_name=key,
                upload_id=upload_id,
            )
        except (S3Error, AttributeError) as exc:
            # Log but don't re-raise — abort is best-effort cleanup
            self._log.error("stream_abort_failed", key=key, error=str(exc))
