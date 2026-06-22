"""SourceRegistry — loads and queries the source catalog."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
import yaml

from geo_bronze.errors import ConfigurationError, RegistryError
from geo_bronze.models.source import ProtocolFamily, Source

logger = structlog.get_logger(__name__)

_DEFAULT_SOURCES_PATH = Path(__file__).parent / "sources.yaml"

# Required metadata keys per protocol_family
_REQUIRED_TILE_META = {"tile_url_template", "tile_format", "axis_order"}


class SourceRegistry:
    """Loads, validates, and queries the data source catalog."""

    def __init__(self, sources_path: Path = _DEFAULT_SOURCES_PATH) -> None:
        self._log = logger.bind(component="SourceRegistry")
        self._sources: dict[str, Source] = {}
        self._load(sources_path)

    def _load(self, path: Path) -> None:
        """Parse sources.yaml and validate all entries."""
        if not path.exists():
            raise RegistryError(f"Sources file not found: {path}")

        try:
            with path.open(encoding="utf-8") as fh:
                raw: dict[str, Any] = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise RegistryError(f"Failed to parse {path}: {exc}") from exc

        records = raw.get("sources", [])
        for entry in records:
            try:
                source = Source.model_validate(entry)
            except Exception as exc:
                raise RegistryError(
                    f"Invalid source entry '{entry.get('source_id', '?')}': {exc}"
                ) from exc
            self._validate_metadata_consistency(source)
            self._sources[source.source_id] = source

        self._log.info("sources_loaded", count=len(self._sources))

    def _validate_metadata_consistency(self, source: Source) -> None:
        """Validate protocol-specific metadata requirements."""
        if source.protocol_family == "tile":
            missing = _REQUIRED_TILE_META - set(source.metadata.keys())
            if missing:
                raise ConfigurationError(
                    f"Source '{source.source_id}' (tile) missing metadata keys: {missing}"
                )
            axis_order = source.metadata.get("axis_order", "zxy")
            template = source.metadata.get("tile_url_template", "")
            if axis_order == "zyx":
                if "{z}/{y}/{x}" not in template and "{z}/{y}/{x}" not in template:
                    # Allow flexible template but warn if clearly inconsistent
                    if "{x}" in template and "{y}" in template and "{z}" in template:
                        # Check ordering in template
                        z_pos = template.index("{z}")
                        y_pos = template.index("{y}")
                        x_pos = template.index("{x}")
                        if not (z_pos < y_pos < x_pos):
                            raise ConfigurationError(
                                f"Source '{source.source_id}': axis_order=zyx but template "
                                f"does not use {{z}}/{{y}}/{{x}} order: {template}"
                            )

    def get(self, source_id: str) -> Source:
        """Return a source by ID.

        Raises:
            RegistryError: If the source is not found.
        """
        try:
            return self._sources[source_id]
        except KeyError:
            raise RegistryError(f"Source not found: '{source_id}'")

    def all_sources(self, include_disabled: bool = False) -> list[Source]:
        """Return all sources, optionally including disabled ones."""
        sources = list(self._sources.values())
        if not include_disabled:
            sources = [s for s in sources if s.enabled]
        return sources

    def find_sources(
        self,
        entity_types: list[str],
        aoi: dict[str, Any] | None = None,
        include_disabled: bool = False,
    ) -> list[Source]:
        """Find sources that cover the requested entity types.

        Args:
            entity_types: List of entity type strings to match.
            aoi: Optional AOI dict for spatial filtering (not implemented in MVP).
            include_disabled: Whether to include disabled sources.

        Returns:
            List of matching Source records.
        """
        results: list[Source] = []
        for source in self.all_sources(include_disabled=include_disabled):
            if any(et in source.entity_types for et in entity_types):
                results.append(source)
        self._log.debug(
            "find_sources",
            entity_types=entity_types,
            found=[s.source_id for s in results],
        )
        return results
