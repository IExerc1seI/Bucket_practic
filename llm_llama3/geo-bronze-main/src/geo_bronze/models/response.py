"""Collector response domain models."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from geo_bronze.decoders.hints import DecoderHint


class CheckResult(BaseModel):
    """Result of a single validation check."""

    passed: bool
    message: str = ""


class ValidationResult(BaseModel):
    """Aggregate result from ValidationAgent."""

    passed: bool
    checks: dict[str, CheckResult] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class CollectorResponse(BaseModel):
    """Response returned by a collector after data retrieval."""

    subtask_id: str
    success: bool
    raw_bytes: bytes | None = None
    """For normal mode. For streaming — None; data is already in bronze."""
    streamed_to_key: str | None = None
    """For streaming mode: the object key in bronze."""
    content_type: str = ""
    content_length: int = 0
    content_hash: str = ""
    """SHA-256 hex digest."""
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    """Original URL, response headers, status code."""
    decoder_hint: DecoderHint = DecoderHint.JSON_GENERIC
    extras: dict[str, Any] = Field(default_factory=dict)
    """For tile family: list of TileRecord; otherwise empty."""
    error: str | None = None

    model_config = {"arbitrary_types_allowed": True}
