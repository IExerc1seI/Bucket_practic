"""Domain models for geo-bronze."""
from geo_bronze.models.response import CheckResult, CollectorResponse, ValidationResult
from geo_bronze.models.sidecar import BronzeSidecar, TileRecord
from geo_bronze.models.source import AuthConfig, ProtocolFamily, Source
from geo_bronze.models.task import (
    AOI,
    ArcGISSubtaskParams,
    CollectionTask,
    HTTPSubtaskParams,
    OGCSubtaskParams,
    Subtask,
    SubtaskParams,
    TileSubtaskParams,
    TimeWindow,
)

__all__ = [
    "AOI",
    "ArcGISSubtaskParams",
    "AuthConfig",
    "BronzeSidecar",
    "CheckResult",
    "CollectionTask",
    "CollectorResponse",
    "HTTPSubtaskParams",
    "OGCSubtaskParams",
    "ProtocolFamily",
    "Source",
    "Subtask",
    "SubtaskParams",
    "TileRecord",
    "TileSubtaskParams",
    "TimeWindow",
    "ValidationResult",
]
