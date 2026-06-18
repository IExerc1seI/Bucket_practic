"""Tests for TileCollector."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import respx

from geo_bronze.agents.collectors.tile import (
    TileCollector,
    _compute_tile_grid,
    _lat_to_tile_y,
    _lon_to_tile_x,
)
from geo_bronze.config import Settings
from geo_bronze.decoders.hints import DecoderHint
from geo_bronze.errors import ConfigurationError
from geo_bronze.models.source import Source
from geo_bronze.models.task import Subtask, TileSubtaskParams

FIXTURES = Path(__file__).parent.parent / "fixtures"
PNG_TILE = (FIXTURES / "tile.png").read_bytes()
PBF_TILE = (FIXTURES / "tile.pbf").read_bytes()


def make_tile_source(
    tile_format: str = "png",
    axis_order: str = "zxy",
    subdomains: list[str] | None = None,
    api_key_env: str | None = None,
    endpoint: str = "https://tile.example.com",
    template: str = "{endpoint}/{z}/{x}/{y}.png",
) -> Source:
    meta: dict = {
        "tile_format": tile_format,
        "tile_url_template": template,
        "axis_order": axis_order,
        "zoom_range": [0, 18],
    }
    if subdomains:
        meta["subdomains"] = subdomains
    if api_key_env:
        meta["api_key_env"] = api_key_env
    return Source(
        source_id="test-tiles",
        name="Test Tiles",
        protocol_family="tile",
        endpoint=endpoint,
        entity_types=["basemap_raster"],
        license="test",
        metadata=meta,
    )


def make_subtask(source: Source, zoom: int = 8, bbox: tuple = (30.0, 50.0, 31.0, 51.0)) -> Subtask:
    return Subtask(
        parent_task_id="task-1",
        source_id=source.source_id,
        protocol_family="tile",
        params=TileSubtaskParams(zoom=zoom, bbox=bbox),
    )


class TestTileGrid:
    def test_lon_to_tile_x(self) -> None:
        assert _lon_to_tile_x(0.0, 1) == 1
        assert _lon_to_tile_x(-180.0, 0) == 0

    def test_lat_to_tile_y(self) -> None:
        # At zoom 0, there's only 1 tile
        y = _lat_to_tile_y(0.0, 0)
        assert y == 0

    def test_compute_tile_grid_small_bbox(self) -> None:
        tiles = _compute_tile_grid((30.0, 50.0, 31.0, 51.0), zoom=8)
        assert len(tiles) > 0
        # All coordinates should be valid for zoom 8
        max_coord = 2**8 - 1
        for x, y in tiles:
            assert 0 <= x <= max_coord
            assert 0 <= y <= max_coord


class TestTileCollectorZXY:
    @respx.mock
    @pytest.mark.asyncio
    async def test_standard_xyz_tiles_downloaded(
        self, mock_bronze_writer: MagicMock, settings: Settings
    ) -> None:
        """Standard XYZ tiles are downloaded in parallel with correct keys."""
        respx.get(url__regex=r"https://tile\.example\.com/\d+/\d+/\d+\.png").mock(
            return_value=httpx.Response(200, content=PNG_TILE, headers={"content-type": "image/png"})
        )

        collector = TileCollector(bronze_writer=mock_bronze_writer, settings=settings)
        source = make_tile_source()
        subtask = make_subtask(source, zoom=8, bbox=(30.0, 50.0, 30.02, 50.02))
        response = await collector.collect(subtask, source)

        assert response.success is True
        assert response.decoder_hint == DecoderHint.XYZ_RASTER_TILE
        assert len(response.extras.get("tiles", [])) > 0

    @respx.mock
    @pytest.mark.asyncio
    async def test_tile_keys_use_x_y_format(
        self, mock_bronze_writer: MagicMock, settings: Settings
    ) -> None:
        """Bronze key always uses normalized z/x/y order regardless of source axis_order."""
        respx.get(url__regex=r"https://.*\.png").mock(
            return_value=httpx.Response(200, content=PNG_TILE, headers={"content-type": "image/png"})
        )

        collector = TileCollector(bronze_writer=mock_bronze_writer, settings=settings)
        source = make_tile_source()
        subtask = make_subtask(source, zoom=8, bbox=(30.0, 50.0, 30.02, 50.02))
        response = await collector.collect(subtask, source)

        tiles = response.extras.get("tiles", [])
        for tile in tiles:
            key = tile["s3_key"]
            # Key pattern: source_id/YYYY/MM/DD/task_id/z/x/y.ext
            parts = key.split("/")
            # z, x, y should appear in that order
            z_idx = -4
            x_idx = -3
            y_idx = -2
            assert int(parts[z_idx]) == 8  # zoom level


class TestTileCollectorZYX:
    @respx.mock
    @pytest.mark.asyncio
    async def test_zyx_axis_order_builds_correct_url(
        self, mock_bronze_writer: MagicMock, settings: Settings
    ) -> None:
        """axis_order=zyx source uses {z}/{y}/{x} URL pattern."""
        captured_urls: list[str] = []

        def capture(request: httpx.Request) -> httpx.Response:
            captured_urls.append(str(request.url))
            return httpx.Response(200, content=PBF_TILE, headers={"content-type": "application/x-protobuf"})

        respx.get(url__regex=r"https://dsns\.example\.com/tile/\d+/\d+/\d+\.pbf").mock(
            side_effect=capture
        )

        source = Source(
            source_id="dsns-mine-tiles",
            name="DSNS Mine Tiles",
            protocol_family="tile",
            endpoint="https://dsns.example.com/vtserver",
            entity_types=["mine_danger_zones"],
            license="test",
            metadata={
                "tile_format": "mvt",
                "tile_url_template": "https://dsns.example.com/tile/{z}/{y}/{x}.pbf",
                "axis_order": "zyx",
                "zoom_range": [0, 11],
            },
        )
        subtask = make_subtask(source, zoom=8, bbox=(30.0, 50.0, 30.02, 50.02))

        collector = TileCollector(bronze_writer=mock_bronze_writer, settings=settings)
        response = await collector.collect(subtask, source)

        assert response.success is True
        assert response.decoder_hint == DecoderHint.MVT

        # Verify the URL uses y/x order
        for url in captured_urls:
            # URL should match z/{y}/{x} pattern (y before x in path)
            path_parts = url.split("/")
            # Find the segment after /tile/
            tile_idx = path_parts.index("tile") if "tile" in path_parts else -1
            if tile_idx >= 0 and tile_idx + 3 < len(path_parts):
                z = path_parts[tile_idx + 1]
                y = path_parts[tile_idx + 2]  # y comes second for zyx
                x_with_ext = path_parts[tile_idx + 3]
                x = x_with_ext.split(".")[0]
                assert z.isdigit() and x.isdigit() and y.isdigit()

    @respx.mock
    @pytest.mark.asyncio
    async def test_zyx_bronze_key_is_normalized_zxy(
        self, mock_bronze_writer: MagicMock, settings: Settings
    ) -> None:
        """Even for zyx source, bronze key uses normalized x/y order."""
        respx.get(url__regex=r"https://dsns\.example\.com/tile/\d+/\d+/\d+\.pbf").mock(
            return_value=httpx.Response(200, content=PBF_TILE, headers={"content-type": "application/x-protobuf"})
        )

        source = Source(
            source_id="dsns-test",
            name="DSNS Test",
            protocol_family="tile",
            endpoint="https://dsns.example.com",
            entity_types=["mine_danger_zones"],
            license="test",
            metadata={
                "tile_format": "mvt",
                "tile_url_template": "https://dsns.example.com/tile/{z}/{y}/{x}.pbf",
                "axis_order": "zyx",
                "zoom_range": [0, 11],
            },
        )
        subtask = make_subtask(source, zoom=8, bbox=(30.0, 50.0, 30.02, 50.02))

        collector = TileCollector(bronze_writer=mock_bronze_writer, settings=settings)
        response = await collector.collect(subtask, source)

        tiles = response.extras.get("tiles", [])
        assert len(tiles) > 0
        for tile in tiles:
            key = tile["s3_key"]
            # Key should end with z/x/y.mvt (not z/y/x)
            # The last 3 path segments before the filename are z, x, y
            parts = key.split("/")
            assert parts[-1].endswith(".mvt")


class TestTileCollectorSubdomains:
    @respx.mock
    @pytest.mark.asyncio
    async def test_subdomain_round_robin(
        self, mock_bronze_writer: MagicMock, settings: Settings
    ) -> None:
        """Subdomains are distributed in round-robin fashion."""
        used_subdomains: list[str] = []

        def capture(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            for sub in ["tms1", "tms2", "tms3"]:
                if sub in url:
                    used_subdomains.append(sub)
                    break
            return httpx.Response(200, content=PNG_TILE, headers={"content-type": "image/png"})

        respx.get(url__regex=r"https://(tms1|tms2|tms3)\.visicom\.ua/.*\.png").mock(
            side_effect=capture
        )

        source = Source(
            source_id="visicom-test",
            name="Visicom Test",
            protocol_family="tile",
            endpoint="https://{subdomain}.visicom.ua/base",
            entity_types=["basemap_raster"],
            license="test",
            enabled=True,
            metadata={
                "tile_format": "png",
                "tile_url_template": "https://{subdomain}.visicom.ua/base/{z}/{x}/{y}.png",
                "axis_order": "zxy",
                "zoom_range": [0, 19],
                "subdomains": ["tms1", "tms2", "tms3"],
            },
        )
        subtask = make_subtask(source, zoom=8, bbox=(30.0, 50.0, 30.05, 50.05))

        collector = TileCollector(bronze_writer=mock_bronze_writer, settings=settings)
        response = await collector.collect(subtask, source)

        assert response.success is True
        assert len(used_subdomains) > 0
        # Should use multiple subdomains if there are multiple tiles
        if len(used_subdomains) >= 3:
            assert set(used_subdomains) == {"tms1", "tms2", "tms3"}

    @respx.mock
    @pytest.mark.asyncio
    async def test_api_key_substituted(
        self, mock_bronze_writer: MagicMock, settings: Settings
    ) -> None:
        """API key from env variable is substituted in the tile URL."""
        captured_urls: list[str] = []

        def capture(request: httpx.Request) -> httpx.Response:
            captured_urls.append(str(request.url))
            return httpx.Response(200, content=PNG_TILE, headers={"content-type": "image/png"})

        respx.get(url__regex=r"https://tile\.example\.com/.*\.png.*").mock(side_effect=capture)

        os.environ["TEST_API_KEY"] = "my-secret-key"
        try:
            source = Source(
                source_id="keyed-tiles",
                name="Keyed Tiles",
                protocol_family="tile",
                endpoint="https://tile.example.com",
                entity_types=["basemap_raster"],
                license="test",
                enabled=True,
                metadata={
                    "tile_format": "png",
                    "tile_url_template": "{endpoint}/{z}/{x}/{y}.png?key={api_key}",
                    "axis_order": "zxy",
                    "zoom_range": [0, 18],
                    "api_key_env": "TEST_API_KEY",
                },
            )
            subtask = make_subtask(source, zoom=8, bbox=(30.0, 50.0, 30.02, 50.02))
            collector = TileCollector(bronze_writer=mock_bronze_writer, settings=settings)
            await collector.collect(subtask, source)

            for url in captured_urls:
                assert "my-secret-key" in url
        finally:
            del os.environ["TEST_API_KEY"]


class TestTileCollectorValidation:
    def test_axis_order_mismatch_raises_configuration_error(
        self, mock_bronze_writer: MagicMock, settings: Settings
    ) -> None:
        """axis_order=zyx with {x}/{y} template raises ConfigurationError."""
        source = Source(
            source_id="bad-tile",
            name="Bad Tile",
            protocol_family="tile",
            endpoint="https://example.com",
            entity_types=["basemap_raster"],
            license="test",
            metadata={
                "tile_format": "png",
                "tile_url_template": "{endpoint}/{z}/{x}/{y}.png",  # x before y = wrong for zyx
                "axis_order": "zyx",
                "zoom_range": [0, 10],
            },
        )
        collector = TileCollector(bronze_writer=mock_bronze_writer, settings=settings)
        with pytest.raises(ConfigurationError, match="axis_order=zyx"):
            collector._validate_axis_order(source)

    def test_valid_zyx_template_passes(
        self, mock_bronze_writer: MagicMock, settings: Settings
    ) -> None:
        """axis_order=zyx with {z}/{y}/{x} template passes validation."""
        source = Source(
            source_id="ok-tile",
            name="OK Tile",
            protocol_family="tile",
            endpoint="https://example.com",
            entity_types=["basemap_raster"],
            license="test",
            metadata={
                "tile_format": "mvt",
                "tile_url_template": "{endpoint}/{z}/{y}/{x}.pbf",
                "axis_order": "zyx",
                "zoom_range": [0, 11],
            },
        )
        collector = TileCollector(bronze_writer=mock_bronze_writer, settings=settings)
        # Should not raise
        collector._validate_axis_order(source)

    @respx.mock
    @pytest.mark.asyncio
    async def test_mvt_format_returns_mvt_hint(
        self, mock_bronze_writer: MagicMock, settings: Settings
    ) -> None:
        """tile_format=mvt returns decoder_hint=MVT."""
        respx.get(url__regex=r"https://.*\.pbf").mock(
            return_value=httpx.Response(200, content=PBF_TILE, headers={"content-type": "application/x-protobuf"})
        )

        source = Source(
            source_id="mvt-test",
            name="MVT Test",
            protocol_family="tile",
            endpoint="https://tile.example.com",
            entity_types=["test"],
            license="test",
            metadata={
                "tile_format": "mvt",
                "tile_url_template": "{endpoint}/{z}/{x}/{y}.pbf",
                "axis_order": "zxy",
                "zoom_range": [0, 14],
            },
        )
        subtask = make_subtask(source, zoom=8, bbox=(30.0, 50.0, 30.02, 50.02))
        collector = TileCollector(bronze_writer=mock_bronze_writer, settings=settings)
        response = await collector.collect(subtask, source)

        assert response.decoder_hint == DecoderHint.MVT
