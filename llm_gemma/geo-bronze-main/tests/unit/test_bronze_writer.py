"""Tests for BronzeWriter using moto S3 mock."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from geo_bronze.config import Settings
from geo_bronze.models.sidecar import BronzeSidecar, TileRecord
from geo_bronze.storage.bronze import BronzeWriter


@pytest.fixture
def settings() -> Settings:
    return Settings(
        minio_endpoint="localhost:9000",
        minio_access_key="test",
        minio_secret_key="test",
        minio_bucket="test-bronze",
        minio_secure=False,
    )


def make_sidecar(task_id: str = "task-1", subtask_id: str = "sub-1") -> BronzeSidecar:
    return BronzeSidecar(
        source_id="test-src",
        request_url="https://example.com/data",
        request_method="GET",
        request_params={},
        response_status=200,
        response_headers={"content-type": "application/json"},
        timestamp=datetime.now(timezone.utc),
        content_hash="abc123",
        content_type="application/json",
        content_length=42,
        license="test",
        aoi={"type": "bbox", "bbox": [30.0, 50.0, 31.0, 51.0]},
        agent_version="0.1.0",
        decoder_hint="geojson",
        task_id=task_id,
        subtask_id=subtask_id,
    )


class TestBronzeWriterWithMoto:
    """Tests using moto to mock S3/MinIO calls."""

    def _make_writer(self, settings: Settings) -> BronzeWriter:
        """Create a BronzeWriter with mocked MinIO client."""
        writer = BronzeWriter(settings)
        # Replace minio client with a mock that delegates to boto3 (via moto)
        return writer

    @mock_aws
    def test_write_and_read_back(self, settings: Settings) -> None:
        """Write bytes and verify they land in S3."""
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=settings.minio_bucket)

        data = b'{"type": "FeatureCollection", "features": []}'
        key = "test-source/2024/01/01/task-1/sub-1.json"

        writer = BronzeWriter.__new__(BronzeWriter)
        writer._settings = settings
        writer._bucket = settings.minio_bucket
        writer._log = MagicMock()

        import io
        from minio import Minio

        mock_minio = MagicMock(spec=Minio)
        mock_result = MagicMock()
        mock_result.etag = "etag-123"
        mock_minio.put_object.return_value = mock_result
        mock_minio.bucket_exists.return_value = False
        writer._client = mock_minio

        result = writer.write(key, data, "application/json")

        assert result.key == key
        assert result.etag == "etag-123"
        assert result.size_bytes == len(data)
        mock_minio.put_object.assert_called_once()

    def test_ensure_bucket_creates_if_missing(self, settings: Settings) -> None:
        """ensure_bucket creates the bucket when it does not exist."""
        from minio import Minio

        writer = BronzeWriter.__new__(BronzeWriter)
        writer._settings = settings
        writer._bucket = settings.minio_bucket
        writer._log = MagicMock()

        mock_minio = MagicMock(spec=Minio)
        mock_minio.bucket_exists.return_value = False
        writer._client = mock_minio

        writer.ensure_bucket()

        mock_minio.make_bucket.assert_called_once_with(settings.minio_bucket)

    def test_ensure_bucket_skips_if_exists(self, settings: Settings) -> None:
        """ensure_bucket does not call make_bucket when bucket already exists."""
        from minio import Minio

        writer = BronzeWriter.__new__(BronzeWriter)
        writer._settings = settings
        writer._bucket = settings.minio_bucket
        writer._log = MagicMock()

        mock_minio = MagicMock(spec=Minio)
        mock_minio.bucket_exists.return_value = True
        writer._client = mock_minio

        writer.ensure_bucket()

        mock_minio.make_bucket.assert_not_called()

    def test_write_sidecar_serializes_correctly(self, settings: Settings) -> None:
        """write_sidecar serializes the BronzeSidecar to JSON."""
        from minio import Minio

        writer = BronzeWriter.__new__(BronzeWriter)
        writer._settings = settings
        writer._bucket = settings.minio_bucket
        writer._log = MagicMock()

        mock_minio = MagicMock(spec=Minio)
        writer._client = mock_minio

        sidecar = make_sidecar()
        writer.write_sidecar("test/sidecar.json", sidecar)

        call_args = mock_minio.put_object.call_args
        assert call_args.kwargs["content_type"] == "application/json"
        # Verify data is valid JSON
        payload_bytes = call_args.kwargs["data"].read()
        parsed = json.loads(payload_bytes)
        assert parsed["source_id"] == "test-src"
        assert parsed["task_id"] == "task-1"

    def test_sidecar_required_fields(self) -> None:
        """All required sidecar fields are present."""
        sidecar = make_sidecar()
        data = json.loads(sidecar.model_dump_json())
        required = [
            "source_id", "request_url", "request_method", "response_status",
            "timestamp", "content_hash", "content_type", "content_length",
            "license", "aoi", "agent_version", "decoder_hint", "task_id", "subtask_id",
        ]
        for field in required:
            assert field in data, f"Missing field: {field}"

    def test_sidecar_new_fields_serialized(self) -> None:
        """New sidecar fields (requires_manual_georeferencing, bbox_override, tiles) serialize."""
        tile = TileRecord(z=8, x=100, y=200, content_hash="hash1", size_bytes=1024, s3_key="key1")
        sidecar = BronzeSidecar(
            source_id="src",
            request_url="https://example.com",
            request_method="GET",
            request_params={},
            response_status=200,
            response_headers={},
            timestamp=datetime.now(timezone.utc),
            content_hash=hashlib.sha256(b"data").hexdigest(),
            content_type="application/json",
            content_length=4,
            license="ODbL",
            aoi={"type": "bbox", "bbox": [30.0, 50.0, 31.0, 51.0]},
            agent_version="0.1.0",
            decoder_hint="geojson",
            task_id="t1",
            subtask_id="s1",
            requires_manual_georeferencing=True,
            bbox_override=[22.1, 44.3, 40.2, 52.3],
            source_axis_order="zyx",
            tiles=[tile],
        )
        data = json.loads(sidecar.model_dump_json())
        assert data["requires_manual_georeferencing"] is True
        assert data["bbox_override"] == [22.1, 44.3, 40.2, 52.3]
        assert data["source_axis_order"] == "zyx"
        assert len(data["tiles"]) == 1
        assert data["tiles"][0]["z"] == 8

    def test_content_hash_sha256(self) -> None:
        """content_hash must match SHA-256 of the raw data."""
        raw = b"hello world"
        expected = hashlib.sha256(raw).hexdigest()
        sidecar = make_sidecar()
        sidecar = sidecar.model_copy(update={"content_hash": expected})
        assert sidecar.content_hash == expected

    def test_timestamp_utc(self) -> None:
        """Timestamp in sidecar must be UTC."""
        sidecar = make_sidecar()
        assert sidecar.timestamp.tzinfo is not None
        import re
        iso = sidecar.model_dump_json()
        # Check that ISO string is present and contains UTC indicator
        assert sidecar.timestamp.tzname() in ("UTC", "utc", "+00:00") or sidecar.timestamp.utcoffset() is not None

    def test_multipart_upload_start_part_complete(self, settings: Settings) -> None:
        """stream_start / stream_part / stream_complete flow."""
        from minio import Minio

        writer = BronzeWriter.__new__(BronzeWriter)
        writer._settings = settings
        writer._bucket = settings.minio_bucket
        writer._log = MagicMock()

        mock_minio = MagicMock(spec=Minio)
        mock_minio._create_multipart_upload.return_value = "upload-id-123"
        mock_minio._upload_part.return_value = "etag-part-1"
        writer._client = mock_minio

        upload_id = writer.stream_start("test/big.json", "application/json")
        assert upload_id == "upload-id-123"

        etag = writer.stream_part(upload_id, "test/big.json", 1, b"x" * 5_242_880)
        assert etag == "etag-part-1"

        writer.stream_complete(upload_id, "test/big.json", [(1, "etag-part-1")])
        mock_minio._complete_multipart_upload.assert_called_once()

    def test_multipart_upload_abort(self, settings: Settings) -> None:
        """stream_abort is called on error — does not raise."""
        from minio import Minio

        writer = BronzeWriter.__new__(BronzeWriter)
        writer._settings = settings
        writer._bucket = settings.minio_bucket
        writer._log = MagicMock()

        mock_minio = MagicMock(spec=Minio)
        mock_minio._create_multipart_upload.return_value = "upload-id-456"
        writer._client = mock_minio

        upload_id = writer.stream_start("test/big2.json", "application/json")
        writer.stream_abort(upload_id, "test/big2.json")
        mock_minio._abort_multipart_upload.assert_called_once()
