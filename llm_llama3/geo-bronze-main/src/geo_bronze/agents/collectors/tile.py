"""TileCollector — handles XYZ raster and Mapbox Vector Tile sources."""
from __future__ import annotations

import asyncio
import hashlib
import itertools
import math
import os
from datetime import datetime, timezone
from typing import ClassVar

import httpx
import structlog

from geo_bronze.agents.collectors.base import BaseCollector
from geo_bronze.config import Settings, get_settings
from geo_bronze.decoders.hints import DecoderHint
from geo_bronze.errors import CollectorError, ConfigurationError
from geo_bronze.models.response import CollectorResponse
from geo_bronze.models.sidecar import TileRecord
from geo_bronze.models.source import ProtocolFamily, Source
from geo_bronze.models.task import Subtask, TileSubtaskParams
from geo_bronze.storage.bronze import BronzeWriter

logger = structlog.get_logger(__name__)

_MAX_TILES = 1000
_CONCURRENCY = 10


def _lon_to_tile_x(lon: float, zoom: int) -> int:
    """Convert longitude to XYZ tile X coordinate."""
    return int((lon + 180.0) / 360.0 * (2**zoom))


def _lat_to_tile_y(lat: float, zoom: int) -> int:
    """Convert latitude to XYZ tile Y coordinate (Web Mercator)."""
    lat_rad = math.radians(lat)
    return int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * (2**zoom))


def _compute_tile_grid(
    bbox: tuple[float, float, float, float], zoom: int
) -> list[tuple[int, int]]:
    """Return list of (x, y) tile coordinates covering a bbox at given zoom."""
    minx, miny, maxx, maxy = bbox
    x_min = _lon_to_tile_x(minx, zoom)
    x_max = _lon_to_tile_x(maxx, zoom)
    y_min = _lat_to_tile_y(maxy, zoom)  # Note: y axis is flipped in Web Mercator
    y_max = _lat_to_tile_y(miny, zoom)

    tiles = []
    for x in range(x_min, x_max + 1):
        for y in range(y_min, y_max + 1):
            max_coord = 2**zoom - 1
            if 0 <= x <= max_coord and 0 <= y <= max_coord:
                tiles.append((x, y))
    return tiles


class TileCollector(BaseCollector):
    """Collects XYZ raster tiles and Mapbox Vector Tiles in parallel."""

    protocol_family: ClassVar[ProtocolFamily] = "tile"

    def __init__(self, bronze_writer: BronzeWriter, settings: Settings | None = None) -> None:
        super().__init__()
        self._writer = bronze_writer
        self._settings = settings or get_settings()

    def _validate_axis_order(self, source: Source) -> None:
        """Verify that axis_order is consistent with tile_url_template.

        Raises:
            ConfigurationError: If axis_order=zyx but template does not use z/y/x ordering.
        """
        meta = source.metadata
        axis_order = meta.get("axis_order", "zxy")
        template = meta.get("tile_url_template", "")

        if axis_order == "zyx":
            if "{z}" in template and "{x}" in template and "{y}" in template:
                z_pos = template.index("{z}")
                y_pos = template.index("{y}")
                x_pos = template.index("{x}")
                if not (z_pos < y_pos < x_pos):
                    raise ConfigurationError(
                        f"Source '{source.source_id}': axis_order=zyx but template does not use "
                        f"{{z}}/{{y}}/{{x}} order. Got: {template}"
                    )

    async def collect(self, subtask: Subtask, source: Source) -> CollectorResponse:
        """Collect a grid of XYZ tiles for the given bbox and zoom level."""
        self._log.info("collect_start", subtask_id=subtask.subtask_id, source=source.source_id)
        assert isinstance(subtask.params, TileSubtaskParams), "TileCollector requires TileSubtaskParams"
        params = subtask.params

        self._validate_axis_order(source)
        meta = source.metadata

        tiles = _compute_tile_grid(params.bbox, params.zoom)
        if len(tiles) > _MAX_TILES:
            self._log.warning(
                "tile_limit_exceeded",
                count=len(tiles),
                limit=_MAX_TILES,
                source=source.source_id,
            )
            tiles = tiles[:_MAX_TILES]

        tile_format: str = meta.get("tile_format", "png")
        axis_order: str = meta.get("axis_order", "zxy")
        template: str = meta.get("tile_url_template", "")
        subdomains: list[str] | None = meta.get("subdomains")
        api_key_env: str | None = meta.get("api_key_env")

        api_key = os.environ.get(api_key_env, "") if api_key_env else ""

        subdomain_cycle = itertools.cycle(subdomains) if subdomains else None

        semaphore = asyncio.Semaphore(_CONCURRENCY)
        now = datetime.now(timezone.utc)
        date_prefix = f"{now.year}/{now.month:02d}/{now.day:02d}"

        async with self._make_client(timeout=30.0) as client:
            tasks = [
                self._download_tile(
                    client=client,
                    semaphore=semaphore,
                    z=params.zoom,
                    x=x,
                    y=y,
                    template=template,
                    source=source,
                    axis_order=axis_order,
                    tile_format=tile_format,
                    api_key=api_key,
                    subdomain=next(subdomain_cycle) if subdomain_cycle else None,
                    subtask_id=subtask.subtask_id,
                    parent_task_id=subtask.parent_task_id,
                    date_prefix=date_prefix,
                )
                for x, y in tiles
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        tile_records: list[TileRecord] = []
        errors: list[str] = []
        for r in results:
            if isinstance(r, TileRecord):
                tile_records.append(r)
            elif isinstance(r, Exception):
                errors.append(str(r))

        decoder_hint = self._tile_format_to_hint(tile_format)
        total_bytes = sum(t.size_bytes for t in tile_records)

        self._log.info(
            "collect_complete",
            subtask_id=subtask.subtask_id,
            tiles=len(tile_records),
            errors=len(errors),
        )

        return CollectorResponse(
            subtask_id=subtask.subtask_id,
            success=len(errors) == 0 or len(tile_records) > 0,
            raw_bytes=None,
            content_type=f"image/{tile_format}" if tile_format in ("png", "jpg", "webp") else "application/x-protobuf",
            content_length=total_bytes,
            content_hash=hashlib.sha256(b"".join(t.content_hash.encode() for t in tile_records)).hexdigest(),
            source_metadata={
                "source": source.source_id,
                "zoom": params.zoom,
                "bbox": list(params.bbox),
                "tile_count": len(tile_records),
                "errors": errors[:10],
            },
            decoder_hint=decoder_hint,
            extras={"tiles": [t.model_dump() for t in tile_records]},
        )

    async def _download_tile(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        z: int,
        x: int,
        y: int,
        template: str,
        source: Source,
        axis_order: str,
        tile_format: str,
        api_key: str,
        subdomain: str | None,
        subtask_id: str,
        parent_task_id: str,
        date_prefix: str,
    ) -> TileRecord:
        """Download and store a single tile."""
        async with semaphore:
            # Build URL with axis-order-aware substitution
            url = template.format(
                endpoint=source.endpoint.rstrip("/"),
                z=z,
                x=x,
                y=y,
                subdomain=subdomain or "",
                api_key=api_key,
            )

            try:
                response = await self._fetch_with_retry(client, "GET", url)
            except Exception as exc:
                raise CollectorError(f"Failed to download tile z={z}/x={x}/y={y}: {exc}") from exc

            data = response.content
            content_hash = self._compute_hash(data)

            # Normalized key always uses x/y regardless of source axis_order
            s3_key = (
                f"{source.source_id}/{date_prefix}/{parent_task_id}"
                f"/{z}/{x}/{y}.{tile_format}"
            )
            self._writer.write(s3_key, data, f"image/{tile_format}")

            return TileRecord(
                z=z,
                x=x,
                y=y,
                content_hash=content_hash,
                size_bytes=len(data),
                s3_key=s3_key,
            )

    @staticmethod
    def _tile_format_to_hint(tile_format: str) -> DecoderHint:
        """Map tile_format string to DecoderHint."""
        if tile_format in ("mvt", "pbf"):
            return DecoderHint.MVT
        return DecoderHint.XYZ_RASTER_TILE
