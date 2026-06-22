"""Bronze sidecar metadata models."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TileRecord(BaseModel):
    """Metadata for a single downloaded tile."""

    z: int
    x: int
    y: int
    content_hash: str
    size_bytes: int
    s3_key: str
    """Full object key in bronze storage."""


class BronzeSidecar(BaseModel):
    """Sidecar JSON written alongside raw bytes in bronze storage."""

    source_id: str
    request_url: str
    request_method: str
    request_params: dict[str, Any] = Field(default_factory=dict)
    response_status: int
    response_headers: dict[str, str] = Field(default_factory=dict)
    timestamp: datetime
    """UTC timestamp in ISO 8601."""
    content_hash: str
    """SHA-256 hex digest."""
    content_type: str
    content_length: int
    license: str
    aoi: dict[str, Any]
    """GeoJSON or bbox dict."""
    agent_version: str
    decoder_hint: str
    """Value of DecoderHint enum."""
    task_id: str
    subtask_id: str
    requires_manual_georeferencing: bool = False
    """For PNG maps without embedded georeferencing."""
    bbox_override: list[float] | None = None
    """Pre-known bbox [minx, miny, maxx, maxy] in EPSG:4326."""
    source_axis_order: str | None = None
    """For tile sources: 'zxy' or 'zyx'."""
    tiles: list[TileRecord] | None = None
    """For tile collections: list of individual tile records."""
    warnings: list[str] = Field(default_factory=list)
    """Validation warnings (e.g. bbox_outside_aoi)."""
