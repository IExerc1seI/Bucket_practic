"""Shared fixtures for geo-bronze tests."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from geo_bronze.config import Settings
from geo_bronze.llm.base import LLMClient
from geo_bronze.models.source import Source

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        minio_endpoint="localhost:9000",
        minio_access_key="test",
        minio_secret_key="test",
        minio_bucket="test-bronze",
        minio_secure=False,
        llm_provider="ollama",
        streaming_threshold_bytes=10_485_760,  # 10 MB for tests
        streaming_chunk_size_bytes=5_242_880,
    )


@pytest.fixture
def mock_llm() -> LLMClient:
    """Mock LLM client that returns a predefined response."""
    mock = AsyncMock()
    mock.complete = AsyncMock(return_value='{"subtasks": []}')
    return mock  # type: ignore[return-value]


@pytest.fixture
def mock_bronze_writer() -> MagicMock:
    """Mock BronzeWriter."""
    writer = MagicMock()
    writer.write.return_value = MagicMock(key="test/key", etag="etag", size_bytes=100)
    writer.write_sidecar.return_value = None
    writer.stream_start.return_value = "upload-id-123"
    writer.stream_part.return_value = "etag-part"
    writer.stream_complete.return_value = None
    writer.stream_abort.return_value = None
    return writer


@pytest.fixture
def osm_overpass_source() -> Source:
    return Source(
        source_id="osm-overpass",
        name="OpenStreetMap — Overpass API",
        protocol_family="http",
        endpoint="https://overpass-api.de/api/interpreter",
        entity_types=["osm_landfills", "osm_brownfields"],
        license="ODbL-1.0",
        metadata={
            "method": "POST",
            "body_template": (
                '[out:json][timeout:60];\narea["name"="{area_name}"]->.searchArea;\n'
                "({tags_query});\nout geom;"
            ),
            "decoder_hint": "overpass-json",
            "entity_to_tags": {
                "osm_landfills": 'way["landuse"="landfill"](area.searchArea);',
            },
        },
    )


@pytest.fixture
def geofabrik_source() -> Source:
    return Source(
        source_id="osm-geofabrik-ukraine",
        name="Geofabrik Ukraine",
        protocol_family="http",
        endpoint="https://download.geofabrik.de/europe/ukraine",
        entity_types=["osm_full_dump"],
        license="ODbL-1.0",
        metadata={
            "streaming": True,
            "files": [{"path": "-latest.osm.pbf", "decoder_hint": "osm-pbf"}],
        },
    )
