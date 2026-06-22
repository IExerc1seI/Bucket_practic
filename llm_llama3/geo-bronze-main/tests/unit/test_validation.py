"""Tests for ValidationAgent."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from geo_bronze.agents.validation import ValidationAgent
from geo_bronze.decoders.hints import DecoderHint
from geo_bronze.models.response import CollectorResponse
from geo_bronze.models.task import AOI, CollectionTask


def make_response(**kwargs: object) -> CollectorResponse:
    defaults = dict(
        subtask_id="sub-1",
        success=True,
        raw_bytes=b'{"type": "FeatureCollection", "features": []}',
        content_type="application/json",
        content_length=500,
        content_hash="abc",
        decoder_hint=DecoderHint.GEOJSON,
    )
    defaults.update(kwargs)  # type: ignore[arg-type]
    return CollectorResponse(**defaults)  # type: ignore[arg-type]


class TestValidationAgent:
    @pytest.mark.asyncio
    async def test_valid_geojson_passes(self) -> None:
        agent = ValidationAgent()
        response = make_response(
            raw_bytes=b'{"type": "FeatureCollection", "features": []}',
            content_length=500,
        )
        result = await agent.run(response)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_html_disguised_as_json_fails(self) -> None:
        """HTML error page masquerading as JSON should fail format check."""
        agent = ValidationAgent()
        html_bytes = b"<!DOCTYPE html><html><body>Error 503</body></html>" + b" " * 200
        response = make_response(
            raw_bytes=html_bytes,
            content_type="application/json",
            content_length=len(html_bytes),
        )
        result = await agent.run(response)
        assert result.passed is False
        assert result.checks["format"].passed is False

    @pytest.mark.asyncio
    async def test_empty_response_fails(self) -> None:
        """Response with very small content fails size check."""
        agent = ValidationAgent()
        response = make_response(
            raw_bytes=b"{}",
            content_length=2,
        )
        result = await agent.run(response)
        assert result.passed is False
        assert result.checks["size"].passed is False

    @pytest.mark.asyncio
    async def test_response_above_2gb_fails(self) -> None:
        """Response claiming to be >2GB fails size check."""
        agent = ValidationAgent()
        response = make_response(
            content_length=3 * 1024 * 1024 * 1024,
        )
        result = await agent.run(response)
        assert result.passed is False
        assert result.checks["size"].passed is False

    @pytest.mark.asyncio
    async def test_correct_geojson_passes_all_checks(self) -> None:
        valid_geojson = (
            b'{"type":"FeatureCollection","features":[{"type":"Feature",'
            b'"geometry":{"type":"Point","coordinates":[30.5,50.5]},'
            b'"properties":{"name":"Test"}}]}'
        )
        agent = ValidationAgent()
        response = make_response(
            raw_bytes=valid_geojson,
            content_length=len(valid_geojson),
        )
        result = await agent.run(response)
        assert result.passed is True
        assert all(c.passed for c in result.checks.values())

    @pytest.mark.asyncio
    async def test_streaming_object_validates_via_range_get(self) -> None:
        """For streamed objects (raw_bytes=None), validation uses range-get of first 512 bytes."""
        mock_writer = MagicMock()
        mock_writer.read_range.return_value = b'{"type": "FeatureCollection", "features": []}' + b" " * 470

        agent = ValidationAgent(bronze_writer=mock_writer)
        response = make_response(
            raw_bytes=None,
            streamed_to_key="some/key.json",
            content_length=10_000_000,
        )
        result = await agent.run(response)

        mock_writer.read_range.assert_called_once_with("some/key.json", 0, 512)
        assert result.checks["size"].passed is True

    @pytest.mark.asyncio
    async def test_bbox_outside_aoi_adds_warning(self) -> None:
        """GeoJSON bbox clearly outside AOI adds warning but does not fail."""
        import json

        data = json.dumps({
            "type": "FeatureCollection",
            "bbox": [100.0, 20.0, 110.0, 30.0],  # Far from Ukraine
            "features": [],
        }).encode()

        task = CollectionTask(
            entity_types=["test"],
            aoi=AOI(type="bbox", bbox=[29.0, 49.0, 40.0, 52.0]),
        )
        agent = ValidationAgent()
        response = make_response(
            raw_bytes=data,
            content_length=len(data),
            decoder_hint=DecoderHint.GEOJSON,
        )
        result = await agent.run(response, task=task)

        assert result.passed is True  # Warning, not failure
        assert "bbox_outside_aoi" in result.warnings

    @pytest.mark.asyncio
    async def test_tile_invalid_coords_fail(self) -> None:
        """Tile with out-of-bounds coordinates fails spatial check."""
        agent = ValidationAgent()
        zoom = 8
        max_coord = 2**zoom - 1
        response = make_response(
            decoder_hint=DecoderHint.XYZ_RASTER_TILE,
            content_length=5000,
            extras={"tiles": [{"z": zoom, "x": max_coord + 1, "y": 0}]},
        )
        result = await agent.run(response)
        assert result.passed is False
