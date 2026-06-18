"""Source registry domain models."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, field_validator


class AuthConfig(BaseModel):
    """Authentication configuration for a source."""

    type: Literal["bearer", "basic", "api_key", "none"] = "none"
    token_env: str | None = None
    """Environment variable name holding the token/key."""
    username_env: str | None = None
    password_env: str | None = None
    header_name: str | None = None
    """Header name for api_key auth type."""


ProtocolFamily = Literal["ogc", "arcgis", "tile", "http"]


class Source(BaseModel):
    """A registered data source entry."""

    source_id: str
    name: str
    protocol_family: ProtocolFamily
    endpoint: str
    auth: AuthConfig | None = None
    entity_types: list[str]
    spatial_coverage: dict[str, Any] | None = None
    license: str
    enabled: bool = True
    metadata: dict[str, Any] = {}

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, v: str) -> str:
        if not v.startswith(("http://", "https://", "https://{subdomain}")):
            # Allow template URLs like https://{subdomain}.example.com/...
            if not ("{" in v and "}" in v):
                raise ValueError(f"endpoint must be a valid URL: {v}")
        return v
