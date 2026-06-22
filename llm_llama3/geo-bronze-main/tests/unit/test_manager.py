"""Tests for ManagerAgent."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from geo_bronze.agents.manager import ManagerAgent
from geo_bronze.agents.validation import ValidationAgent
from geo_bronze.config import Settings
from geo_bronze.decoders.hints import DecoderHint
from geo_bronze.models.response import CollectorResponse, ValidationResult, CheckResult
from geo_bronze.models.source import Source
from geo_bronze.models.task import AOI, CollectionTask, HTTPSubtaskParams, Subtask


def make_source(source_id: str = "osm-overpass", entity_types: list | None = None) -> Source:
    return Source(
        source_id=source_id,
        name=f"Test Source {source_id}",
        protocol_family="http",
        endpoint="https://example.com",
        entity_types=entity_types or ["test"],
        license="test",
        metadata={
            "entity_to_tags": {
                "osm_landfills": 'way["landuse"="landfill"](area.searchArea);',
            },
            "body_template": '[out:json];\narea["name"="{area_name}"]->.s;\n({tags_query});\nout;',
        },
    )


def make_task(entity_types: list | None = None, area_name: str = "Чернігівська область") -> CollectionTask:
    return CollectionTask(
        entity_types=entity_types or ["osm_landfills"],
        aoi=AOI(type="named", name=area_name),
    )


class TestManagerDecomposition:
    @pytest.mark.asyncio
    async def test_decomposes_to_subtasks(self, settings: Settings) -> None:
        """ManagerAgent calls LLM and returns parsed subtasks."""
        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(return_value=json.dumps({
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
        }))

        registry = MagicMock()
        registry.find_sources.return_value = [make_source()]
        registry.get.return_value = make_source()

        collector = AsyncMock()
        collector.collect = AsyncMock(return_value=CollectorResponse(
            subtask_id="sub-1",
            success=True,
            raw_bytes=b'{"version":0.6,"elements":[]}',
            content_type="application/json",
            content_length=500,
            content_hash="abc123",
            decoder_hint=DecoderHint.OVERPASS_JSON,
        ))

        validator = ValidationAgent()
        writer = MagicMock()
        writer.write.return_value = MagicMock(key="k", etag="e", size_bytes=10)

        manager = ManagerAgent(
            llm=mock_llm,
            registry=registry,
            collectors={"http": collector},
            validator=validator,
            bronze_writer=writer,
            settings=settings,
        )

        task = make_task()
        report = await manager.run(task)

        assert report.subtasks_total == 1
        mock_llm.complete.assert_called_once()
        collector.collect.assert_called_once()

    @pytest.mark.asyncio
    async def test_overpass_uses_entity_to_tags_map(self, settings: Settings) -> None:
        """For osm_landfills, LLM response uses tags from entity_to_tags map."""
        expected_tags = 'way["landuse"="landfill"](area.searchArea);'

        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(return_value=json.dumps({
            "subtasks": [
                {
                    "source_id": "osm-overpass",
                    "protocol_family": "http",
                    "params": {
                        "method": "POST",
                        "body_template_vars": {
                            "area_name": "Чернігівська область",
                            "tags_query": expected_tags,
                        },
                    },
                }
            ]
        }))

        overpass_source = make_source(entity_types=["osm_landfills"])
        registry = MagicMock()
        registry.find_sources.return_value = [overpass_source]
        registry.get.return_value = overpass_source

        executed_subtasks: list[Subtask] = []

        async def capture_collect(subtask: Subtask, source: Source) -> CollectorResponse:
            executed_subtasks.append(subtask)
            return CollectorResponse(
                subtask_id=subtask.subtask_id,
                success=True,
                raw_bytes=b'{"version":0.6,"elements":[]}',
                content_type="application/json",
                content_length=500,
                content_hash="abc",
                decoder_hint=DecoderHint.OVERPASS_JSON,
            )

        collector = MagicMock()
        collector.collect = capture_collect

        writer = MagicMock()
        writer.write.return_value = MagicMock(key="k", etag="e", size_bytes=10)

        manager = ManagerAgent(
            llm=mock_llm,
            registry=registry,
            collectors={"http": collector},
            validator=ValidationAgent(),
            bronze_writer=writer,
            settings=settings,
        )

        await manager.run(make_task(["osm_landfills"]))

        assert len(executed_subtasks) == 1
        st = executed_subtasks[0]
        assert isinstance(st.params, HTTPSubtaskParams)
        btvars = st.params.body_template_vars or {}
        assert btvars.get("tags_query") == expected_tags
        assert btvars.get("area_name") == "Чернігівська область"

    @pytest.mark.asyncio
    async def test_routes_by_protocol_family_not_if_elif(self, settings: Settings) -> None:
        """ManagerAgent uses dict-based routing, not if-elif."""
        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(return_value=json.dumps({
            "subtasks": [
                {
                    "source_id": "test-http",
                    "protocol_family": "http",
                    "params": {},
                },
                {
                    "source_id": "test-arcgis",
                    "protocol_family": "arcgis",
                    "params": {"service_path": "/FeatureServer/0"},
                },
            ]
        }))

        http_source = Source(
            source_id="test-http", name="HTTP", protocol_family="http",
            endpoint="https://example.com", entity_types=["test"], license="test", metadata={}
        )
        arcgis_source = Source(
            source_id="test-arcgis", name="ArcGIS", protocol_family="arcgis",
            endpoint="https://example.com/arcgis", entity_types=["test"], license="test", metadata={}
        )

        registry = MagicMock()
        registry.find_sources.return_value = [http_source, arcgis_source]
        registry.get.side_effect = lambda sid: http_source if sid == "test-http" else arcgis_source

        http_collector = MagicMock()
        http_collector.collect = AsyncMock(return_value=CollectorResponse(
            subtask_id="s1", success=True, raw_bytes=b'{"k":"v"}' * 100,
            content_type="application/json", content_length=500,
            content_hash="a", decoder_hint=DecoderHint.JSON_GENERIC,
        ))
        arcgis_collector = MagicMock()
        arcgis_collector.collect = AsyncMock(return_value=CollectorResponse(
            subtask_id="s2", success=True, raw_bytes=b'{"features":[]}' * 40,
            content_type="application/json", content_length=500,
            content_hash="b", decoder_hint=DecoderHint.ESRI_JSON_JSONL,
        ))

        writer = MagicMock()
        writer.write.return_value = MagicMock(key="k", etag="e", size_bytes=10)

        manager = ManagerAgent(
            llm=mock_llm,
            registry=registry,
            collectors={"http": http_collector, "arcgis": arcgis_collector},
            validator=ValidationAgent(),
            bronze_writer=writer,
            settings=settings,
        )

        task = CollectionTask(
            entity_types=["test"],
            aoi=AOI(type="bbox", bbox=[30.0, 50.0, 31.0, 51.0]),
        )
        report = await manager.run(task)

        http_collector.collect.assert_called_once()
        arcgis_collector.collect.assert_called_once()
        assert report.subtasks_success == 2

    @pytest.mark.asyncio
    async def test_no_candidates_returns_empty_report(self, settings: Settings) -> None:
        """When registry finds no candidates, returns report with zero subtasks."""
        mock_llm = AsyncMock()

        registry = MagicMock()
        registry.find_sources.return_value = []

        writer = MagicMock()

        manager = ManagerAgent(
            llm=mock_llm,
            registry=registry,
            collectors={},
            validator=ValidationAgent(),
            bronze_writer=writer,
            settings=settings,
        )

        task = make_task(["unknown_entity"])
        report = await manager.run(task)

        assert report.subtasks_total == 0
        mock_llm.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_parse_error_returns_empty_subtasks(self, settings: Settings) -> None:
        """If LLM returns invalid JSON, decompose returns empty list gracefully."""
        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(return_value="not valid json at all")

        registry = MagicMock()
        registry.find_sources.return_value = [make_source()]

        writer = MagicMock()

        manager = ManagerAgent(
            llm=mock_llm,
            registry=registry,
            collectors={},
            validator=ValidationAgent(),
            bronze_writer=writer,
            settings=settings,
        )

        task = make_task()
        report = await manager.run(task)
        assert report.subtasks_total == 0
