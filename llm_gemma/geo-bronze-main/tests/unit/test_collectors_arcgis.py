"""Tests for ArcGISCollector."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from geo_bronze.agents.collectors.arcgis import ArcGISCollector, _PAGE_SIZE
from geo_bronze.decoders.hints import DecoderHint
from geo_bronze.models.source import Source
from geo_bronze.models.task import ArcGISSubtaskParams, Subtask


def make_source() -> Source:
    return Source(
        source_id="test-arcgis",
        name="Test ArcGIS Service",
        protocol_family="arcgis",
        endpoint="https://example.com/arcgis/rest/services/TestService",
        entity_types=["test_features"],
        license="test",
        metadata={},
    )


def make_subtask(source: Source) -> Subtask:
    return Subtask(
        parent_task_id="task-1",
        source_id=source.source_id,
        protocol_family="arcgis",
        params=ArcGISSubtaskParams(service_path="/FeatureServer/0"),
    )


def make_page_response(features_count: int, exceeded: bool = False) -> bytes:
    features = [{"attributes": {"OBJECTID": i}, "geometry": {"x": 30.0, "y": 50.0}} for i in range(features_count)]
    return json.dumps({
        "objectIdFieldName": "OBJECTID",
        "features": features,
        "exceededTransferLimit": exceeded,
    }).encode()


class TestArcGISCollector:
    @respx.mock
    @pytest.mark.asyncio
    async def test_single_page_response(self) -> None:
        """Single page (no pagination) returns ESRI_JSON_JSONL hint."""
        data = make_page_response(5, exceeded=False)
        respx.get("https://example.com/arcgis/rest/services/TestService/FeatureServer/0/query").mock(
            return_value=httpx.Response(200, content=data, headers={"content-type": "application/json"})
        )

        collector = ArcGISCollector()
        source = make_source()
        subtask = make_subtask(source)
        response = await collector.collect(subtask, source)

        assert response.success is True
        assert response.decoder_hint == DecoderHint.ESRI_JSON_JSONL

    @respx.mock
    @pytest.mark.asyncio
    async def test_three_pages_pagination(self) -> None:
        """Three paginated pages are fetched and merged into JSONL."""
        url = "https://example.com/arcgis/rest/services/TestService/FeatureServer/0/query"

        call_count = 0

        def page_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            offset = int(request.url.params.get("resultOffset", 0))
            if offset == 0:
                return httpx.Response(200, content=make_page_response(_PAGE_SIZE, exceeded=True))
            elif offset == _PAGE_SIZE:
                return httpx.Response(200, content=make_page_response(_PAGE_SIZE, exceeded=True))
            else:
                return httpx.Response(200, content=make_page_response(500, exceeded=False))

        respx.get(url).mock(side_effect=page_handler)

        collector = ArcGISCollector()
        source = make_source()
        subtask = make_subtask(source)
        response = await collector.collect(subtask, source)

        assert response.success is True
        assert response.decoder_hint == DecoderHint.ESRI_JSON_JSONL
        # JSONL has lines separated by newlines
        lines = [l for l in (response.raw_bytes or b"").split(b"\n") if l.strip()]
        assert len(lines) == 3  # 3 page responses

    @respx.mock
    @pytest.mark.asyncio
    async def test_where_clause_passed(self) -> None:
        """WHERE clause from SubtaskParams is passed to query."""
        captured: dict = {}

        def capture(request: httpx.Request) -> httpx.Response:
            captured["params"] = dict(request.url.params)
            return httpx.Response(200, content=make_page_response(1, exceeded=False))

        respx.get("https://example.com/arcgis/rest/services/TestService/FeatureServer/0/query").mock(
            side_effect=capture
        )

        collector = ArcGISCollector()
        source = make_source()
        subtask = Subtask(
            parent_task_id="t1",
            source_id=source.source_id,
            protocol_family="arcgis",
            params=ArcGISSubtaskParams(
                service_path="/FeatureServer/0",
                where="STATUS='active'",
            ),
        )
        await collector.collect(subtask, source)
        assert captured["params"].get("where") == "STATUS='active'"
