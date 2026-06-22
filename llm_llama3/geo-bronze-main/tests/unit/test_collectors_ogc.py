"""Tests for OGCCollector."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import respx

from geo_bronze.agents.collectors.ogc import OGCCollector
from geo_bronze.decoders.hints import DecoderHint
from geo_bronze.errors import CollectorError
from geo_bronze.models.source import Source
from geo_bronze.models.task import OGCSubtaskParams, Subtask

FIXTURES = Path(__file__).parent.parent / "fixtures"


def make_wfs_source() -> Source:
    return Source(
        source_id="test-wfs",
        name="Test WFS",
        protocol_family="ogc",
        endpoint="https://example.com/wfs",
        entity_types=["test_features"],
        license="test",
        metadata={},
    )


def make_wfs_subtask(
    source: Source,
    service: str = "WFS",
    operation: str = "GetFeature",
    output_format: str = "application/gml+xml",
    type_name: str | None = "test:Feature",
) -> Subtask:
    return Subtask(
        parent_task_id="task-1",
        source_id=source.source_id,
        protocol_family="ogc",
        params=OGCSubtaskParams(
            service=service,  # type: ignore[arg-type]
            operation=operation,
            output_format=output_format,
            type_name=type_name,
        ),
    )


class TestOGCCollectorWFS:
    @respx.mock
    @pytest.mark.asyncio
    async def test_wfs_getfeature_gml_response(self) -> None:
        """WFS GetFeature with GML Content-Type returns decoder_hint=GML."""
        gml_data = (FIXTURES / "wfs_getfeature.gml").read_bytes()
        respx.get("https://example.com/wfs").mock(
            return_value=httpx.Response(
                200,
                content=gml_data,
                headers={"content-type": "application/gml+xml; version=3.2"},
            )
        )

        collector = OGCCollector()
        source = make_wfs_source()
        subtask = make_wfs_subtask(source)
        response = await collector.collect(subtask, source)

        assert response.success is True
        assert response.decoder_hint == DecoderHint.GML
        assert response.raw_bytes == gml_data

    @respx.mock
    @pytest.mark.asyncio
    async def test_wfs_getfeature_json_response(self) -> None:
        """WFS GetFeature with JSON Content-Type returns decoder_hint=GEOJSON."""
        geojson = b'{"type": "FeatureCollection", "features": []}'
        respx.get("https://example.com/wfs").mock(
            return_value=httpx.Response(
                200,
                content=geojson,
                headers={"content-type": "application/json"},
            )
        )

        collector = OGCCollector()
        source = make_wfs_source()
        subtask = make_wfs_subtask(source, output_format="application/json")
        response = await collector.collect(subtask, source)

        assert response.success is True
        assert response.decoder_hint == DecoderHint.GEOJSON

    @pytest.mark.asyncio
    async def test_wms_raises_not_implemented(self) -> None:
        """Non-WFS service raises NotImplementedError."""
        collector = OGCCollector()
        source = make_wfs_source()
        subtask = make_wfs_subtask(source, service="WMS")

        with pytest.raises(NotImplementedError, match="WMS"):
            await collector.collect(subtask, source)

    @pytest.mark.asyncio
    async def test_wcs_raises_not_implemented(self) -> None:
        """WCS service raises NotImplementedError."""
        collector = OGCCollector()
        source = make_wfs_source()
        subtask = make_wfs_subtask(source, service="WCS")

        with pytest.raises(NotImplementedError):
            await collector.collect(subtask, source)

    @respx.mock
    @pytest.mark.asyncio
    async def test_wfs_bbox_included_in_query(self) -> None:
        """BBOX parameter is included in the WFS request when specified."""
        captured: dict = {}

        def capture(request: httpx.Request) -> httpx.Response:
            captured["params"] = dict(request.url.params)
            return httpx.Response(200, content=b"<wfs:FC/>", headers={"content-type": "text/xml"})

        respx.get("https://example.com/wfs").mock(side_effect=capture)

        collector = OGCCollector()
        source = make_wfs_source()
        subtask = Subtask(
            parent_task_id="t1",
            source_id=source.source_id,
            protocol_family="ogc",
            params=OGCSubtaskParams(
                service="WFS",
                operation="GetFeature",
                type_name="test:Feature",
                bbox=(30.0, 50.0, 31.0, 51.0),
            ),
        )
        await collector.collect(subtask, source)
        assert "BBOX" in captured.get("params", {})
