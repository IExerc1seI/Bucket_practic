"""ValidationAgent — deterministic validation of collected data."""
from __future__ import annotations

import json
from typing import ClassVar

import structlog

from geo_bronze.agents.base import BaseAgent
from geo_bronze.decoders.hints import DecoderHint
from geo_bronze.models.response import CheckResult, CollectorResponse, ValidationResult
from geo_bronze.models.task import CollectionTask
from geo_bronze.storage.bronze import BronzeWriter

logger = structlog.get_logger(__name__)

_HTML_SIGNATURES = (b"<!DOCTYPE html", b"<!doctype html", b"<html", b"<HTML")
_MIN_CONTENT_LENGTH = 100
_MAX_CONTENT_LENGTH = 2 * 1024 * 1024 * 1024  # 2 GB

_JSON_DECODER_HINTS = {
    DecoderHint.GEOJSON,
    DecoderHint.JSON_GENERIC,
    DecoderHint.JSON_POINTS,
    DecoderHint.JSON_WEATHER_FMI,
    DecoderHint.OVERPASS_JSON,
    DecoderHint.ESRI_JSON,
    DecoderHint.ESRI_JSON_JSONL,
}


class ValidationAgent(BaseAgent):
    """Performs three deterministic validation checks on CollectorResponse."""

    name: ClassVar[str] = "ValidationAgent"
    version: ClassVar[str] = "0.1.0"

    def __init__(self, bronze_writer: BronzeWriter | None = None) -> None:
        super().__init__(llm=None)
        self._writer = bronze_writer

    async def run(
        self,
        response: CollectorResponse,
        task: CollectionTask | None = None,
    ) -> ValidationResult:
        """Validate a CollectorResponse.

        Args:
            response: The collector response to validate.
            task: Original collection task (used for spatial validation).

        Returns:
            ValidationResult with check details and pass/fail.
        """
        self._log.debug("validate_start", subtask_id=response.subtask_id)
        checks: dict[str, CheckResult] = {}
        warnings: list[str] = []

        # Check 1: Format / HTML detection
        checks["format"] = self._check_format(response)

        # Check 2: Size bounds
        checks["size"] = self._check_size(response)

        # Check 3: Spatial coverage (non-blocking — adds warning)
        spatial_check, spatial_warnings = self._check_spatial(response, task)
        checks["spatial"] = spatial_check
        warnings.extend(spatial_warnings)

        passed = all(c.passed for c in checks.values())
        self._log.info(
            "validate_complete",
            subtask_id=response.subtask_id,
            passed=passed,
            warnings=warnings,
        )
        return ValidationResult(passed=passed, checks=checks, warnings=warnings)

    def _get_first_bytes(self, response: CollectorResponse) -> bytes:
        """Get first 512 bytes from response — from memory or bronze range-get."""
        if response.raw_bytes is not None:
            return response.raw_bytes[:512]
        if response.streamed_to_key and self._writer:
            try:
                return self._writer.read_range(response.streamed_to_key, 0, 512)
            except Exception:
                return b""
        return b""

    def _check_format(self, response: CollectorResponse) -> CheckResult:
        """Check 1: Verify content is not an HTML error page."""
        first_bytes = self._get_first_bytes(response)
        first_lower = first_bytes.lower()

        for sig in _HTML_SIGNATURES:
            if sig.lower() in first_lower:
                # It's HTML — was HTML expected?
                if "html" not in response.content_type.lower():
                    return CheckResult(
                        passed=False,
                        message=f"Received HTML response but expected {response.content_type}",
                    )

        # For JSON decoder hints, try to verify the content starts as JSON
        if response.decoder_hint in _JSON_DECODER_HINTS and response.raw_bytes is not None:
            snippet = response.raw_bytes[:50].lstrip()
            if snippet and snippet[0:1] not in (b"{", b"["):
                return CheckResult(
                    passed=False,
                    message=f"Expected JSON but content starts with: {snippet[:20]!r}",
                )

        return CheckResult(passed=True, message="Format check passed")

    def _check_size(self, response: CollectorResponse) -> CheckResult:
        """Check 2: Content length is within acceptable bounds."""
        size = response.content_length
        if size < _MIN_CONTENT_LENGTH:
            return CheckResult(
                passed=False,
                message=f"Content too small: {size} bytes (min {_MIN_CONTENT_LENGTH})",
            )
        if size > _MAX_CONTENT_LENGTH:
            return CheckResult(
                passed=False,
                message=f"Content too large: {size} bytes (max {_MAX_CONTENT_LENGTH})",
            )
        return CheckResult(passed=True, message=f"Size OK: {size} bytes")

    def _check_spatial(
        self, response: CollectorResponse, task: CollectionTask | None
    ) -> tuple[CheckResult, list[str]]:
        """Check 3: Spatial coverage check (non-blocking, adds warnings)."""
        warnings: list[str] = []

        # For tile family: validate tile coordinates
        if response.decoder_hint in (DecoderHint.XYZ_RASTER_TILE, DecoderHint.MVT):
            tiles = response.extras.get("tiles", [])
            for tile in tiles:
                z = tile.get("z", 0)
                max_coord = 2**z - 1
                if not (0 <= tile.get("x", 0) <= max_coord) or not (0 <= tile.get("y", 0) <= max_coord):
                    return CheckResult(
                        passed=False, message=f"Tile coordinates out of bounds for z={z}"
                    ), warnings

        # For GeoJSON/Esri-JSON: check bbox against AOI
        if response.decoder_hint in (DecoderHint.GEOJSON, DecoderHint.ESRI_JSON) and response.raw_bytes:
            try:
                data = json.loads(response.raw_bytes[:65536])
                bbox = data.get("bbox")
                if bbox and task and task.aoi.bbox:
                    aoi_bbox = task.aoi.bbox
                    if (
                        bbox[2] < aoi_bbox[0] or bbox[0] > aoi_bbox[2]
                        or bbox[3] < aoi_bbox[1] or bbox[1] > aoi_bbox[3]
                    ):
                        warnings.append("bbox_outside_aoi")
            except (json.JSONDecodeError, IndexError, TypeError):
                pass

        return CheckResult(passed=True, message="Spatial check passed"), warnings
