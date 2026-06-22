"""Tests for SourceRegistry."""
from __future__ import annotations

from pathlib import Path

import pytest

from geo_bronze.errors import ConfigurationError, RegistryError
from geo_bronze.registry.registry import SourceRegistry

SOURCES_YAML = Path(__file__).parent.parent.parent / "src" / "geo_bronze" / "registry" / "sources.yaml"


class TestSourceRegistryLoading:
    def test_loads_all_9_sources(self) -> None:
        registry = SourceRegistry(SOURCES_YAML)
        all_sources = registry.all_sources(include_disabled=True)
        assert len(all_sources) == 9

    def test_all_sources_parse_without_errors(self) -> None:
        # Should not raise
        registry = SourceRegistry(SOURCES_YAML)
        assert registry is not None

    def test_tile_sources_have_required_metadata(self) -> None:
        registry = SourceRegistry(SOURCES_YAML)
        tile_sources = [s for s in registry.all_sources(include_disabled=True) if s.protocol_family == "tile"]
        assert len(tile_sources) >= 3  # dsns-mine-tiles, visicom-basemap, osm-tiles-ua
        for s in tile_sources:
            assert "tile_url_template" in s.metadata, f"{s.source_id} missing tile_url_template"
            assert "tile_format" in s.metadata, f"{s.source_id} missing tile_format"
            assert "axis_order" in s.metadata, f"{s.source_id} missing axis_order"

    def test_disabled_source_filtered_by_default(self) -> None:
        registry = SourceRegistry(SOURCES_YAML)
        enabled = registry.all_sources()
        source_ids = [s.source_id for s in enabled]
        assert "visicom-basemap" not in source_ids

    def test_disabled_source_included_with_flag(self) -> None:
        registry = SourceRegistry(SOURCES_YAML)
        all_sources = registry.all_sources(include_disabled=True)
        source_ids = [s.source_id for s in all_sources]
        assert "visicom-basemap" in source_ids


class TestSourceRegistryFind:
    def test_find_osm_landfills(self) -> None:
        registry = SourceRegistry(SOURCES_YAML)
        results = registry.find_sources(entity_types=["osm_landfills"])
        ids = [s.source_id for s in results]
        assert "osm-overpass" in ids
        # Should not return unrelated sources
        assert "dsns-mine-tiles" not in ids

    def test_find_mine_danger_zones(self) -> None:
        registry = SourceRegistry(SOURCES_YAML)
        results = registry.find_sources(entity_types=["mine_danger_zones"])
        ids = [s.source_id for s in results]
        assert "dsns-mine-tiles" in ids
        assert "osm-overpass" not in ids

    def test_find_returns_only_enabled_by_default(self) -> None:
        registry = SourceRegistry(SOURCES_YAML)
        results = registry.find_sources(entity_types=["basemap_raster"])
        ids = [s.source_id for s in results]
        assert "visicom-basemap" not in ids  # disabled
        assert "osm-tiles-ua" in ids

    def test_find_multiple_entity_types(self) -> None:
        registry = SourceRegistry(SOURCES_YAML)
        results = registry.find_sources(entity_types=["osm_full_dump", "mine_danger_zones"])
        ids = [s.source_id for s in results]
        assert "osm-geofabrik-ukraine" in ids
        assert "dsns-mine-tiles" in ids

    def test_get_by_id(self) -> None:
        registry = SourceRegistry(SOURCES_YAML)
        source = registry.get("osm-overpass")
        assert source.source_id == "osm-overpass"
        assert source.protocol_family == "http"

    def test_get_missing_raises(self) -> None:
        registry = SourceRegistry(SOURCES_YAML)
        with pytest.raises(RegistryError, match="not found"):
            registry.get("nonexistent-source")


class TestSourceRegistryValidation:
    def test_invalid_yaml_raises_registry_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("sources:\n  - {source_id: x\n  invalid yaml !@#", encoding="utf-8")
        with pytest.raises(RegistryError):
            SourceRegistry(bad)

    def test_missing_file_raises_registry_error(self, tmp_path: Path) -> None:
        with pytest.raises(RegistryError, match="not found"):
            SourceRegistry(tmp_path / "nonexistent.yaml")

    def test_tile_source_missing_template_raises(self, tmp_path: Path) -> None:
        bad_yaml = tmp_path / "bad_tile.yaml"
        bad_yaml.write_text(
            """
sources:
  - source_id: bad-tile
    name: Bad Tile
    protocol_family: tile
    endpoint: https://example.com
    entity_types: [basemap_raster]
    license: test
    enabled: true
    metadata:
      tile_format: png
      axis_order: zxy
""",
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError, match="tile_url_template"):
            SourceRegistry(bad_yaml)

    def test_dsns_mine_tiles_axis_order_zyx(self) -> None:
        registry = SourceRegistry(SOURCES_YAML)
        source = registry.get("dsns-mine-tiles")
        assert source.metadata["axis_order"] == "zyx"
        assert "{y}/{x}" in source.metadata["tile_url_template"]

    def test_osm_overpass_entity_to_tags(self) -> None:
        registry = SourceRegistry(SOURCES_YAML)
        source = registry.get("osm-overpass")
        entity_to_tags = source.metadata.get("entity_to_tags", {})
        assert "osm_landfills" in entity_to_tags
        assert "osm_brownfields" in entity_to_tags
        assert "osm_ruins" in entity_to_tags
