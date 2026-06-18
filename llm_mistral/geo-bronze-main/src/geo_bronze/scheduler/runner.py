"""Collection task runner — wires up agents and runs a CollectionTask."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
import yaml

from geo_bronze.agents.collectors.arcgis import ArcGISCollector
from geo_bronze.agents.collectors.http import HTTPCollector
from geo_bronze.agents.collectors.ogc import OGCCollector
from geo_bronze.agents.collectors.tile import TileCollector
from geo_bronze.agents.manager import ManagerAgent, TaskReport
from geo_bronze.agents.validation import ValidationAgent
from geo_bronze.config import Settings, get_settings
from geo_bronze.llm.factory import get_llm_client
from geo_bronze.models.task import AOI, CollectionTask, TimeWindow
from geo_bronze.registry.registry import SourceRegistry
from geo_bronze.storage.bronze import BronzeWriter

logger = structlog.get_logger(__name__)


def _setup_logging(settings: Settings) -> None:
    """Configure structlog based on settings."""
    import logging
    import sys

    import structlog

    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    if settings.log_format == "json":
        processors = [
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    else:
        processors = [
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ]

    structlog.configure(
        processors=processors,  # type: ignore[arg-type]
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
    )


def load_task_from_yaml(path: Path) -> CollectionTask:
    """Load a CollectionTask from a YAML file.

    Args:
        path: Path to the task YAML file.

    Returns:
        Parsed CollectionTask.
    """
    with path.open(encoding="utf-8") as fh:
        data: dict[str, Any] = yaml.safe_load(fh)

    # Parse AOI
    aoi_raw = data.get("aoi", {})
    if isinstance(aoi_raw, list):
        aoi = AOI(type="bbox", bbox=aoi_raw)
    elif isinstance(aoi_raw, dict):
        aoi_type = aoi_raw.get("type", "bbox")
        if aoi_type == "bbox":
            aoi = AOI(type="bbox", bbox=aoi_raw.get("bbox") or aoi_raw.get("coordinates"))
        elif aoi_type == "named":
            aoi = AOI(type="named", name=aoi_raw.get("name", ""))
        else:
            aoi = AOI(type="polygon", geometry=aoi_raw)
    else:
        aoi = AOI(type="bbox", bbox=[22.0, 44.0, 40.0, 52.5])

    # Parse time window
    tw_raw = data.get("time_window")
    time_window = None
    if tw_raw:
        from datetime import datetime

        time_window = TimeWindow(
            start=datetime.fromisoformat(tw_raw["start"]),
            end=datetime.fromisoformat(tw_raw["end"]) if tw_raw.get("end") else None,
        )

    return CollectionTask(
        entity_types=data.get("entity_types", []),
        aoi=aoi,
        time_window=time_window,
        params=data.get("params", {}),
    )


def build_manager(settings: Settings | None = None) -> ManagerAgent:
    """Wire up the full ManagerAgent with all dependencies.

    Args:
        settings: Optional settings override.

    Returns:
        Configured ManagerAgent ready to run tasks.
    """
    settings = settings or get_settings()
    _setup_logging(settings)

    llm = get_llm_client(settings)
    registry = SourceRegistry()
    writer = BronzeWriter(settings)
    writer.ensure_bucket()

    collectors = {
        "http": HTTPCollector(bronze_writer=writer, settings=settings),
        "tile": TileCollector(bronze_writer=writer, settings=settings),
        "ogc": OGCCollector(),
        "arcgis": ArcGISCollector(),
    }
    validator = ValidationAgent(bronze_writer=writer)

    return ManagerAgent(
        llm=llm,
        registry=registry,
        collectors=collectors,  # type: ignore[arg-type]
        validator=validator,
        bronze_writer=writer,
        settings=settings,
    )


async def run_task(task: CollectionTask, settings: Settings | None = None) -> TaskReport:
    """Run a single CollectionTask end-to-end.

    Args:
        task: The collection task to execute.
        settings: Optional settings override.

    Returns:
        TaskReport with execution summary.
    """
    manager = build_manager(settings)
    return await manager.run(task)


async def run_task_from_file(path: Path, settings: Settings | None = None) -> TaskReport:
    """Load a task from YAML and run it.

    Args:
        path: Path to the task YAML file.
        settings: Optional settings override.

    Returns:
        TaskReport with execution summary.
    """
    task = load_task_from_yaml(path)
    logger.info("task_loaded", path=str(path), task_id=task.task_id)
    return await run_task(task, settings)
