"""Tests for HTTPCollector."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import respx
import httpx

from geo_bronze.agents.collectors.http import HTTPCollector
from geo_bronze.config import Settings
from geo_bronze.decoders.hints import DecoderHint
from geo_bronze.models.source import Source
from geo_bronze.models.task import HTTPSubtaskParams, Subtask

FIXTURES = Path(__file__).parent.parent / "fixtures"


def make_source(**kwargs: object) -> Source:
    defaults = dict(
        source_id="test-http",
        name="Test HTTP Source",
        protocol_family="http",
        endpoint="https://example.com",
        entity_types=["test"],
        license="test",
        metadata={},
    )
    defaults.update(kwargs)  # type: ignore[arg-type]
    return Source(**defaults)  # type: ignore[arg-type]


def make_subtask(source: Source, **params_kwargs: object) -> Subtask:
    defaults: dict = dict(protocol_family="http")
    defaults.update(params_kwargs)
    return Subtask(
        parent_task_id="task-1",
        source_id=source.source_id,
        protocol_family="http",
        params=HTTPSubtaskParams(**defaults),  # type: ignore[arg-type]
    )


class TestHTTPCollectorGET:
    @respx.mock
    @pytest.mark.asyncio
    async def test_get_geojson(self, mock_bronze_writer: MagicMock, settings: Settings) -> None:
        """GET request with GeoJSON response returns GEOJSON decoder_hint."""
        geojson = json.dumps({
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "geometry": None, "properties": {}}]
        }).encode()
        respx.get("https://example.com/data").mock(
            return_value=httpx.Response(200, content=geojson, headers={"content-type": "application/json"})
        )

        collector = HTTPCollector(bronze_writer=mock_bronze_writer, settings=settings)
        source = make_source()
        subtask = make_subtask(source, path="/data")
        response = await collector.collect(subtask, source)

        assert response.success is True
        assert response.decoder_hint == DecoderHint.GEOJSON
        assert response.raw_bytes == geojson

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_zip_returns_shapefile_hint(
        self, mock_bronze_writer: MagicMock, settings: Settings
    ) -> None:
        """GET request for a .zip URL returns SHAPEFILE_ZIP hint."""
        data = b"PK\x03\x04fake zip data"
        respx.get("https://example.com/data.shp.zip").mock(
            return_value=httpx.Response(200, content=data, headers={"content-type": "application/zip"})
        )

        collector = HTTPCollector(bronze_writer=mock_bronze_writer, settings=settings)
        source = make_source(endpoint="https://example.com/data.shp.zip")
        subtask = make_subtask(source)
        response = await collector.collect(subtask, source)

        assert response.decoder_hint == DecoderHint.SHAPEFILE_ZIP


class TestHTTPCollectorPOST:
    @respx.mock
    @pytest.mark.asyncio
    async def test_post_with_body_template(
        self, mock_bronze_writer: MagicMock, settings: Settings
    ) -> None:
        """POST with body_template and body_template_vars constructs correct body."""
        overpass_response = (FIXTURES / "overpass_response.json").read_bytes()
        posted_body: bytes | None = None

        def capture_request(request: httpx.Request) -> httpx.Response:
            nonlocal posted_body
            posted_body = request.content
            return httpx.Response(
                200, content=overpass_response, headers={"content-type": "application/json"}
            )

        respx.post("https://overpass-api.de/api/interpreter").mock(side_effect=capture_request)

        source = Source(
            source_id="osm-overpass",
            name="Overpass",
            protocol_family="http",
            endpoint="https://overpass-api.de/api/interpreter",
            entity_types=["osm_landfills"],
            license="ODbL-1.0",
            metadata={
                "body_template": '[out:json];\narea["name"="{area_name}"]->.s;\n({tags_query});\nout;',
                "decoder_hint": "overpass-json",
            },
        )
        subtask = Subtask(
            parent_task_id="task-1",
            source_id="osm-overpass",
            protocol_family="http",
            params=HTTPSubtaskParams(
                method="POST",
                body_template_vars={
                    "area_name": "Чернігівська область",
                    "tags_query": 'way["landuse"="landfill"](area.s);',
                },
            ),
        )

        collector = HTTPCollector(bronze_writer=mock_bronze_writer, settings=settings)
        response = await collector.collect(subtask, source)

        assert response.success is True
        assert response.decoder_hint == DecoderHint.OVERPASS_JSON
        assert b"Чернігівська область" in (posted_body or b"")

    @respx.mock
    @pytest.mark.asyncio
    async def test_post_with_dict_body_serialized_as_json(
        self, mock_bronze_writer: MagicMock, settings: Settings
    ) -> None:
        """POST with dict body sends JSON-encoded body."""
        captured: dict = {}

        def capture(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content
            captured["ct"] = request.headers.get("content-type", "")
            return httpx.Response(200, json={"result": "ok"})

        respx.post("https://example.com/api").mock(side_effect=capture)

        source = make_source(endpoint="https://example.com")
        subtask = make_subtask(source, method="POST", path="/api", body={"key": "value"})

        collector = HTTPCollector(bronze_writer=mock_bronze_writer, settings=settings)
        response = await collector.collect(subtask, source)

        assert b'"key"' in captured.get("body", b"")
        assert "application/json" in captured.get("ct", "")


class TestHTTPCollectorDecoderHints:
    def test_overpass_json_detection(
        self, mock_bronze_writer: MagicMock, settings: Settings
    ) -> None:
        """Body with 'elements' and 'version' fields gets OVERPASS_JSON hint."""
        collector = HTTPCollector(bronze_writer=mock_bronze_writer, settings=settings)
        source = make_source()
        raw = b'{"version": 0.6, "elements": [{"type": "way"}]}'
        from geo_bronze.models.task import HTTPSubtaskParams

        params = HTTPSubtaskParams()
        hint = collector._determine_decoder_hint("https://overpass-api.de/", "application/json", raw, params, source)
        assert hint == DecoderHint.OVERPASS_JSON

    def test_osm_pbf_from_url(
        self, mock_bronze_writer: MagicMock, settings: Settings
    ) -> None:
        """URL ending in .osm.pbf gets OSM_PBF hint."""
        collector = HTTPCollector(bronze_writer=mock_bronze_writer, settings=settings)
        source = make_source()
        from geo_bronze.models.task import HTTPSubtaskParams

        params = HTTPSubtaskParams()
        hint = collector._determine_decoder_hint(
            "https://download.geofabrik.de/europe/ukraine-latest.osm.pbf",
            "application/octet-stream",
            b"",
            params,
            source,
        )
        assert hint == DecoderHint.OSM_PBF

    def test_png_with_bbox_override(
        self, mock_bronze_writer: MagicMock, settings: Settings
    ) -> None:
        """PNG URL with bbox_override in metadata returns PNG_WITH_BBOX_METADATA."""
        collector = HTTPCollector(bronze_writer=mock_bronze_writer, settings=settings)
        source = make_source(metadata={"bbox_override": [22.1, 44.3, 40.2, 52.3]})
        from geo_bronze.models.task import HTTPSubtaskParams

        params = HTTPSubtaskParams()
        hint = collector._determine_decoder_hint(
            "https://www.meteo.gov.ua/f/fire/Fire_Current.png",
            "image/png",
            b"",
            params,
            source,
        )
        assert hint == DecoderHint.PNG_WITH_BBOX_METADATA

    def test_png_without_bbox_override(
        self, mock_bronze_writer: MagicMock, settings: Settings
    ) -> None:
        """PNG without bbox_override returns PNG_GEOREFERENCED_UNKNOWN."""
        collector = HTTPCollector(bronze_writer=mock_bronze_writer, settings=settings)
        source = make_source()
        from geo_bronze.models.task import HTTPSubtaskParams

        params = HTTPSubtaskParams()
        hint = collector._determine_decoder_hint(
            "https://example.com/map.png",
            "image/png",
            b"",
            params,
            source,
        )
        assert hint == DecoderHint.PNG_GEOREFERENCED_UNKNOWN


class TestHTTPCollectorStreaming:
    @respx.mock
    @pytest.mark.asyncio
    async def test_streaming_upload_computes_correct_sha256(
        self, settings: Settings
    ) -> None:
        """Streaming mode computes SHA-256 incrementally and it matches reference."""
        chunk1 = b"A" * 5_242_880
        chunk2 = b"B" * 1_000_000
        all_data = chunk1 + chunk2
        expected_hash = hashlib.sha256(all_data).hexdigest()

        settings_s = Settings(
            minio_endpoint="localhost:9000",
            minio_access_key="test",
            minio_secret_key="test",
            minio_bucket="test-bronze",
            minio_secure=False,
            streaming_threshold_bytes=1,  # Force streaming
            streaming_chunk_size_bytes=5_242_880,
        )

        respx.get("https://example.com/big.osm.pbf").mock(
            return_value=httpx.Response(200, content=all_data, headers={"content-type": "application/octet-stream"})
        )

        writer = MagicMock()
        writer.stream_start.return_value = "uid-1"
        writer.stream_part.return_value = "etag-p"
        writer.stream_complete.return_value = None

        source = make_source(endpoint="https://example.com/big.osm.pbf")
        subtask = Subtask(
            parent_task_id="t1",
            source_id="test-http",
            protocol_family="http",
            params=HTTPSubtaskParams(force_streaming=True),
        )

        collector = HTTPCollector(bronze_writer=writer, settings=settings_s)
        response = await collector.collect(subtask, source)

        assert response.success is True
        assert response.content_hash == expected_hash
        assert response.raw_bytes is None
        assert response.streamed_to_key is not None

    @respx.mock
    @pytest.mark.asyncio
    async def test_streaming_abort_on_error(self, settings: Settings) -> None:
        """stream_abort is called when streaming fails; sidecar is not written."""
        settings_s = Settings(
            minio_endpoint="localhost:9000",
            minio_access_key="test",
            minio_secret_key="test",
            minio_bucket="test-bronze",
            minio_secure=False,
            streaming_threshold_bytes=1,
            streaming_chunk_size_bytes=5_242_880,
        )

        respx.get("https://example.com/big.bin").mock(
            return_value=httpx.Response(500, content=b"error")
        )

        writer = MagicMock()
        writer.stream_start.return_value = "uid-abort"

        source = make_source(endpoint="https://example.com/big.bin")
        subtask = Subtask(
            parent_task_id="t1",
            source_id="test-http",
            protocol_family="http",
            params=HTTPSubtaskParams(force_streaming=True),
        )

        collector = HTTPCollector(bronze_writer=writer, settings=settings_s)
        from geo_bronze.errors import StreamingError

        with pytest.raises(StreamingError):
            await collector.collect(subtask, source)

        # stream_abort must be called
        writer.stream_abort.assert_called_once()
        # write_sidecar must NOT be called
        writer.write_sidecar.assert_not_called()
