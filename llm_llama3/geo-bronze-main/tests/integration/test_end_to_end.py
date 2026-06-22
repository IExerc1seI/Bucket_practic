"""End-to-end integration test: full pipeline with mocked HTTP and MinIO."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from geo_bronze.agents.collectors.http import HTTPCollector
from geo_bronze.agents.collectors.ogc import OGCCollector
from geo_bronze.agents.collectors.arcgis import ArcGISCollector
from geo_bronze.agents.collectors.tile import TileCollector
from geo_bronze.agents.manager import ManagerAgent
from geo_bronze.agents.validation import ValidationAgent
from geo_bronze.config import Settings
from geo_bronze.decoders.hints import DecoderHint
from geo_bronze.models.task import AOI, CollectionTask
from geo_bronze.registry.registry import SourceRegistry
from geo_bronze.storage.bronze import BronzeWriter

FIXTURES = Path(__file__).parent.parent / "fixtures"
SOURCES_YAML = Path(__file__).parent.parent.parent / "src" / "geo_bronze" / "registry" / "sources.yaml"


@pytest.fixture
def mock_writer() -> MagicMock:
    writer = MagicMock(spec=BronzeWriter)
    writer.write.return_value = MagicMock(key="k", etag="etag", size_bytes=100)
    writer.write_sidecar.return_value = None
    writer.stream_start.return_value = "upload-id"
    writer.stream_part.return_value = "part-etag"
    writer.stream_complete.return_value = None
    writer.stream_abort.return_value = None
    writer.read_range.return_value = b'{"version": 0.6, "elements": []}' + b" " * 480
    return writer


@pytest.fixture
def settings() -> Settings:
    return Settings(
        minio_endpoint="localhost:9000",
        minio_access_key="test",
        minio_secret_key="test",
        minio_bucket="test-bronze",
        minio_secure=False,
        llm_provider="ollama",
        streaming_threshold_bytes=52_428_800,
        streaming_chunk_size_bytes=5_242_880,
    )


def build_test_manager(
    mock_llm_response: str,
    mock_writer: MagicMock,
    settings: Settings,
) -> ManagerAgent:
    """Build a ManagerAgent with mocked LLM and storage."""
    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(return_value=mock_llm_response)

    registry = SourceRegistry(SOURCES_YAML)

    collectors = {
        "http": HTTPCollector(bronze_writer=mock_writer, settings=settings),
        "tile": TileCollector(bronze_writer=mock_writer, settings=settings),
        "ogc": OGCCollector(),
        "arcgis": ArcGISCollector(),
    }
    validator = ValidationAgent(bronze_writer=mock_writer)

    return ManagerAgent(
        llm=mock_llm,
        registry=registry,
        collectors=collectors,  # type: ignore[arg-type]
        validator=validator,
        bronze_writer=mock_writer,
        settings=settings,
    )


class TestEndToEndOverpass:
    @respx.mock
    @pytest.mark.asyncio
    async def test_osm_landfills_full_pipeline(
        self, mock_writer: MagicMock, settings: Settings
    ) -> None:
        """Full pipeline: task → LLM → HTTP collector → validator → bronze write."""
        overpass_data = (FIXTURES / "overpass_response.json").read_bytes()

        respx.post("https://overpass-api.de/api/interpreter").mock(
            return_value=httpx.Response(
                200,
                content=overpass_data,
                headers={"content-type": "application/json"},
            )
        )

        llm_response = json.dumps({
            "subtasks": [
                {
                    "source_id": "osm-overpass",
                    "protocol_family": "http",
                    "params": {
                        "method": "POST",
                        "body_template_vars": {
                            "area_name": "Чернігівська область",
                            "tags_query": 'way["landuse"="landfill"](area.searchArea);',
                        },
                    },
                }
            ]
        })

        manager = build_test_manager(llm_response, mock_writer, settings)
        task = CollectionTask(
            entity_types=["osm_landfills"],
            aoi=AOI(type="named", name="Чернігівська область"),
        )

        report = await manager.run(task)

        assert report.subtasks_success == 1
        assert report.subtasks_failed == 0

        # Verify objects were written to bronze
        mock_writer.write.assert_called()
        mock_writer.write_sidecar.assert_called()

        # Verify sidecar content
        sidecar_call = mock_writer.write_sidecar.call_args
        sidecar_key = sidecar_call[0][0]
        sidecar = sidecar_call[0][1]

        assert sidecar_key.endswith(".sidecar.json")
        assert sidecar.source_id == "osm-overpass"
        assert sidecar.decoder_hint == DecoderHint.OVERPASS_JSON.value
        assert sidecar.task_id == task.task_id


class TestEndToEndRegistry:
    @pytest.mark.asyncio
    async def test_registry_sources_loaded(self) -> None:
        """All 9 sources from sources.yaml load correctly."""
        registry = SourceRegistry(SOURCES_YAML)
        all_sources = registry.all_sources(include_disabled=True)
        assert len(all_sources) == 9

    @pytest.mark.asyncio
    async def test_find_sources_for_task(self) -> None:
        """find_sources returns correct candidates for a given entity_types."""
        registry = SourceRegistry(SOURCES_YAML)
        sources = registry.find_sources(["osm_landfills"])
        assert any(s.source_id == "osm-overpass" for s in sources)

    @pytest.mark.asyncio
    async def test_no_sources_for_unknown_entity(self) -> None:
        """find_sources returns empty list for unknown entity_type."""
        registry = SourceRegistry(SOURCES_YAML)
        sources = registry.find_sources(["unknown_entity_xyz"])
        assert len(sources) == 0


class TestEndToEndValidation:
    @respx.mock
    @pytest.mark.asyncio
    async def test_html_response_rejected(
        self, mock_writer: MagicMock, settings: Settings
    ) -> None:
        """HTML error page from source causes validation failure, nothing written."""
        html_response = b"<!DOCTYPE html><html><body>503 Service Unavailable</body></html>"

        respx.post("https://overpass-api.de/api/interpreter").mock(
            return_value=httpx.Response(
                200,
                content=html_response,
                headers={"content-type": "application/json"},
            )
        )

        llm_response = json.dumps({
            "subtasks": [
                {
                    "source_id": "osm-overpass",
                    "protocol_family": "http",
                    "params": {
                        "method": "POST",
                        "body_template_vars": {
                            "area_name": "Test",
                            "tags_query": 'way["test"="yes"](area.searchArea);',
                        },
                    },
                }
            ]
        })

        manager = build_test_manager(llm_response, mock_writer, settings)
        task = CollectionTask(
            entity_types=["osm_landfills"],
            aoi=AOI(type="named", name="Test"),
        )

        report = await manager.run(task)

        # Validation should fail — sidecar not written
        assert report.subtasks_failed >= 1
        mock_writer.write_sidecar.assert_not_called()
