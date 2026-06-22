"""Collection task domain models."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class TimeWindow(BaseModel):
    """Time range for data collection."""

    start: datetime
    end: datetime | None = None


class AOI(BaseModel):
    """Area of Interest — bbox, GeoJSON Polygon, or named area."""

    type: Literal["bbox", "polygon", "named"]
    bbox: list[float] | None = None
    """[minx, miny, maxx, maxy] in EPSG:4326."""
    geometry: dict[str, Any] | None = None
    """GeoJSON Polygon geometry object."""
    name: str | None = None
    """Named area, e.g. 'Чернігівська область'."""

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, v: list[float] | None) -> list[float] | None:
        if v is not None and len(v) != 4:
            raise ValueError("bbox must have exactly 4 elements [minx, miny, maxx, maxy]")
        return v

    def to_dict(self) -> dict[str, Any]:
        """Serialize AOI to plain dict for sidecar storage."""
        if self.type == "bbox" and self.bbox:
            return {"type": "bbox", "bbox": self.bbox}
        if self.type == "polygon" and self.geometry:
            return {"type": "polygon", "geometry": self.geometry}
        if self.type == "named" and self.name:
            return {"type": "named", "name": self.name}
        return self.model_dump(exclude_none=True)


class HTTPSubtaskParams(BaseModel):
    """Parameters for HTTP-family subtasks."""

    protocol_family: Literal["http"] = "http"
    method: Literal["GET", "POST"] = "GET"
    path: str = ""
    query_params: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | bytes | dict[str, Any] | None = None
    body_template_vars: dict[str, str] | None = None
    force_streaming: bool = False
    decoder_hint: str | None = None


class TileSubtaskParams(BaseModel):
    """Parameters for Tile-family subtasks."""

    protocol_family: Literal["tile"] = "tile"
    zoom: int
    bbox: tuple[float, float, float, float]


class ArcGISSubtaskParams(BaseModel):
    """Parameters for ArcGIS-family subtasks."""

    protocol_family: Literal["arcgis"] = "arcgis"
    service_path: str
    where: str = "1=1"
    geometry: tuple[float, float, float, float] | None = None
    out_fields: str = "*"
    out_sr: int = 4326


class OGCSubtaskParams(BaseModel):
    """Parameters for OGC-family subtasks."""

    protocol_family: Literal["ogc"] = "ogc"
    service: Literal["WFS", "WMS", "WCS", "CSW"]
    version: str = "2.0.0"
    operation: str
    type_name: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    output_format: str = "application/json"


SubtaskParams = HTTPSubtaskParams | TileSubtaskParams | ArcGISSubtaskParams | OGCSubtaskParams


class Subtask(BaseModel):
    """A single atomic data collection unit targeting one source."""

    subtask_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    parent_task_id: str
    source_id: str
    protocol_family: Literal["ogc", "arcgis", "tile", "http"]
    params: SubtaskParams


class CollectionTask(BaseModel):
    """High-level data collection task."""

    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    aoi: AOI
    time_window: TimeWindow | None = None
    entity_types: list[str]
    params: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
