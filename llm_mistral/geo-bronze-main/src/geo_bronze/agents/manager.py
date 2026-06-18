"""ManagerAgent — decomposes CollectionTask into Subtasks and orchestrates execution."""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar

import structlog

from geo_bronze.agents.base import BaseAgent
from geo_bronze.agents.collectors.base import BaseCollector
from geo_bronze.agents.validation import ValidationAgent
from geo_bronze.config import Settings, get_settings
from geo_bronze.decoders.hints import DecoderHint
from geo_bronze.errors import LLMError
from geo_bronze.llm.base import LLMClient
from geo_bronze.llm.prompts import MANAGER_SYSTEM_PROMPT
from geo_bronze.models.response import CollectorResponse
from geo_bronze.models.sidecar import BronzeSidecar, TileRecord
from geo_bronze.models.source import ProtocolFamily, Source
from geo_bronze.models.task import (
    AOI,
    ArcGISSubtaskParams,
    CollectionTask,
    HTTPSubtaskParams,
    OGCSubtaskParams,
    Subtask,
    TileSubtaskParams,
)
from geo_bronze.registry.registry import SourceRegistry
from geo_bronze.storage.bronze import BronzeWriter

logger = structlog.get_logger(__name__)


@dataclass
class TaskReport:
    """Summary of a completed collection task execution."""

    task_id: str
    started_at: datetime
    finished_at: datetime
    subtasks_total: int
    subtasks_success: int
    subtasks_failed: int
    written_keys: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class ManagerAgent(BaseAgent):
    """Orchestrates the full collection pipeline for a CollectionTask."""

    name: ClassVar[str] = "ManagerAgent"
    version: ClassVar[str] = "0.1.0"

    def __init__(
        self,
        llm: LLMClient,
        registry: SourceRegistry,
        collectors: dict[ProtocolFamily, BaseCollector],
        validator: ValidationAgent,
        bronze_writer: BronzeWriter,
        settings: Settings | None = None,
    ) -> None:
        super().__init__(llm=llm)
        self._registry = registry
        self._collectors = collectors
        self._validator = validator
        self._writer = bronze_writer
        self._settings = settings or get_settings()

    async def run(self, task: CollectionTask) -> TaskReport:
        """Execute a CollectionTask end-to-end.

        Args:
            task: The high-level collection task.

        Returns:
            TaskReport summarizing execution.
        """
        started_at = datetime.now(timezone.utc)
        self._log.info("task_start", task_id=task.task_id, entity_types=task.entity_types)

        # 1. Find candidate sources
        candidates = self._registry.find_sources(task.entity_types, aoi=task.aoi.to_dict())
        self._log.info("candidates_found", count=len(candidates), task_id=task.task_id)

        if not candidates:
            self._log.warning("no_candidates", task_id=task.task_id, entity_types=task.entity_types)
            return TaskReport(
                task_id=task.task_id,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                subtasks_total=0,
                subtasks_success=0,
                subtasks_failed=0,
            )

        # 2. LLM decomposition into subtasks
        subtasks = await self._decompose_task(task, candidates)
        self._log.info("decomposed", subtasks=len(subtasks), task_id=task.task_id)

        # 3. Execute all subtasks in parallel
        results = await asyncio.gather(
            *[self._execute_subtask(st, task) for st in subtasks],
            return_exceptions=True,
        )

        # 4. Collect results
        written_keys: list[str] = []
        errors: list[str] = []
        successes = 0

        for st, result in zip(subtasks, results):
            if isinstance(result, Exception):
                errors.append(f"{st.subtask_id}: {result}")
            elif isinstance(result, list):
                written_keys.extend(result)
                successes += 1
            else:
                errors.append(f"{st.subtask_id}: unexpected result type")

        finished_at = datetime.now(timezone.utc)
        self._log.info(
            "task_complete",
            task_id=task.task_id,
            success=successes,
            failed=len(errors),
        )
        return TaskReport(
            task_id=task.task_id,
            started_at=started_at,
            finished_at=finished_at,
            subtasks_total=len(subtasks),
            subtasks_success=successes,
            subtasks_failed=len(errors),
            written_keys=written_keys,
            errors=errors,
        )

    async def _decompose_task(
        self, task: CollectionTask, candidates: list[Source]
    ) -> list[Subtask]:
        """Use LLM to decompose a CollectionTask into concrete Subtasks."""
        sources_json = json.dumps(
            [
                {
                    "source_id": s.source_id,
                    "name": s.name,
                    "protocol_family": s.protocol_family,
                    "endpoint": s.endpoint,
                    "entity_types": s.entity_types,
                    "metadata": s.metadata,
                }
                for s in candidates
            ],
            ensure_ascii=False,
            indent=2,
        )

        user_message = json.dumps(
            {
                "task_id": task.task_id,
                "entity_types": task.entity_types,
                "aoi": task.aoi.to_dict(),
                "params": task.params,
                "available_sources": json.loads(sources_json),
            },
            ensure_ascii=False,
            indent=2,
        )

        try:
            raw = await self._llm.complete(  # type: ignore[union-attr]
                system=MANAGER_SYSTEM_PROMPT,
                user=user_message,
                response_schema={"type": "object"},
                temperature=0.0,
            )
        except LLMError as exc:
            self._log.error("llm_error", task_id=task.task_id, error=str(exc))
            return []

        return self._parse_subtasks(raw, task)

    def _parse_subtasks(self, llm_response: str, task: CollectionTask) -> list[Subtask]:
        """Parse LLM response into list of Subtask objects."""
        try:
            data = json.loads(llm_response) 
            if isinstance(data, dict):
                if "<output>" in data:
                    data = data["<output>"]
                elif "output" in data:
                    data = data["output"]

            raw_subtasks = data.get("subtasks", [])

        except (json.JSONDecodeError, AttributeError) as exc:
            self._log.warning("llm_parse_error", error=str(exc), response=llm_response[:200])
            return []

        subtasks: list[Subtask] = []
        for raw in raw_subtasks:
            try:
                protocol_family = raw.get("protocol_family", "http")
                params_data = raw.get("params", {})
                if "body_template" in params_data:
                    self._log.warning("llm_legacy_format_detected")
                    area_name = params_data.get("body_template_vars", {}).get("area_name", "Чернігівська область")
                    query = f"""[out:json][timeout:60];area["name"="{area_name}"]->.searchArea;(way["landuse"="landfill"](area.searchArea);
                relation["landuse"="landfill"](area.searchArea););out geom;"""
                    params_data.clear()
                    params_data["method"] = "POST"
                    params_data["body"] = query


                params_data["protocol_family"] = protocol_family


                # If the LLM emitted a full endpoint URL, convert it to a relative path
                # by stripping the source's base endpoint prefix.
                if "endpoint" in params_data:
                    source = self._registry.get(raw.get("source_id", ""))
                    if source:
                        base = source.endpoint.rstrip("/")
                        full = params_data.pop("endpoint").rstrip("/")
                        if full.startswith(base):
                            rel = full[len(base):].lstrip("/")
                            if rel and not params_data.get("path"):
                                params_data["path"] = rel
                    else:
                        params_data.pop("endpoint")

                params = self._build_params(protocol_family, params_data)
                subtask = Subtask(
                    subtask_id=str(uuid.uuid4()),
                    parent_task_id=task.task_id,
                    source_id=raw["source_id"],
                    protocol_family=protocol_family,
                    params=params,
                )
                subtasks.append(subtask)
            except Exception as exc:
                self._log.warning("subtask_parse_error", error=str(exc), raw=raw)

        return subtasks

    @staticmethod
    def _build_params(
        protocol_family: str, data: dict[str, Any]
    ) -> HTTPSubtaskParams | TileSubtaskParams | ArcGISSubtaskParams | OGCSubtaskParams:
        """Build typed SubtaskParams from raw dict."""
        if protocol_family == "http":
            known = set(HTTPSubtaskParams.model_fields.keys()) | {"protocol_family"}
            extras = {k: str(v) for k, v in data.items() if k not in known}
            cleaned = {k: v for k, v in data.items() if k in known}
            if extras:
                cleaned["query_params"] = {**extras, **cleaned.get("query_params", {})}
            return HTTPSubtaskParams(**cleaned)
        if protocol_family == "tile":
            return TileSubtaskParams(**{k: v for k, v in data.items() if k != "protocol_family"})
        if protocol_family == "arcgis":
            return ArcGISSubtaskParams(**{k: v for k, v in data.items() if k != "protocol_family"})
        if protocol_family == "ogc":
            return OGCSubtaskParams(**{k: v for k, v in data.items() if k != "protocol_family"})
        return HTTPSubtaskParams(**{k: v for k, v in data.items() if k != "protocol_family"})

    async def _execute_subtask(
        self, subtask: Subtask, task: CollectionTask
    ) -> list[str]:
        """Execute a single subtask: collect → validate → write to bronze.

        Returns:
            List of written S3 keys.
        """
        self._log.info("subtask_start", subtask_id=subtask.subtask_id, source=subtask.source_id)

        collector = self._collectors.get(subtask.protocol_family)  # type: ignore[arg-type]
        if collector is None:
            raise ValueError(f"No collector registered for protocol_family: {subtask.protocol_family}")

        source = self._registry.get(subtask.source_id)

        # Collect
        response = await collector.collect(subtask, source)
        if not response.success:
            raise ValueError(f"Collection failed: {response.error}")

        # Validate
        validation = await self._validator.run(response, task=task)
        if not validation.passed:
            failed = {k: v.message for k, v in validation.checks.items() if not v.passed}
            raise ValueError(f"Validation failed: {failed}")

        # Write to bronze
        return await self._write_to_bronze(response, subtask, source, task, validation.warnings)

    async def _write_to_bronze(
        self,
        response: CollectorResponse,
        subtask: Subtask,
        source: Source,
        task: CollectionTask,
        warnings: list[str],
    ) -> list[str]:
        """Write collector response and sidecar to bronze storage."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        date_prefix = f"{now.year}/{now.month:02d}/{now.day:02d}"
        ext = self._mime_to_ext(response.content_type)
        written: list[str] = []

        if response.raw_bytes is not None:
            # Normal mode: write raw bytes
            data_key = (
                f"{source.source_id}/{date_prefix}"
                f"/{task.task_id}/{subtask.subtask_id}.{ext}"
            )
            self._writer.write(data_key, response.raw_bytes, response.content_type)
            written.append(data_key)
        elif response.streamed_to_key:
            written.append(response.streamed_to_key)

        # Write sidecar
        sidecar_key = (
            f"{source.source_id}/{date_prefix}"
            f"/{task.task_id}/{subtask.subtask_id}.sidecar.json"
        )
        tiles: list[TileRecord] | None = None
        if response.extras.get("tiles"):
            tiles = [TileRecord(**t) for t in response.extras["tiles"]]

        sidecar = BronzeSidecar(
            source_id=source.source_id,
            request_url=response.source_metadata.get("url", source.endpoint),
            request_method=subtask.params.method if hasattr(subtask.params, "method") else "GET",
            request_params=dict(subtask.params.query_params) if hasattr(subtask.params, "query_params") else {},
            response_status=response.source_metadata.get("status_code", 200),
            response_headers=response.source_metadata.get("headers", {}),
            timestamp=now,
            content_hash=response.content_hash,
            content_type=response.content_type,
            content_length=response.content_length,
            license=source.license,
            aoi=task.aoi.to_dict(),
            agent_version=self._settings.agent_version,
            decoder_hint=response.decoder_hint.value,
            task_id=task.task_id,
            subtask_id=subtask.subtask_id,
            requires_manual_georeferencing=source.metadata.get("requires_manual_georeferencing", False),
            bbox_override=source.metadata.get("bbox_override"),
            source_axis_order=source.metadata.get("axis_order"),
            tiles=tiles,
            warnings=warnings,
        )
        self._writer.write_sidecar(sidecar_key, sidecar)
        written.append(sidecar_key)

        self._log.info("subtask_written", subtask_id=subtask.subtask_id, keys=written)
        return written

    @staticmethod
    def _mime_to_ext(content_type: str) -> str:
        """Map MIME type to file extension."""
        mapping = {
            "application/json": "json",
            "application/gml+xml": "gml",
            "text/xml": "xml",
            "application/xml": "xml",
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/webp": "webp",
            "image/tiff": "tif",
            "application/x-protobuf": "pbf",
            "application/octet-stream": "bin",
        }
        ct = content_type.lower().split(";")[0].strip()
        return mapping.get(ct, "bin")
