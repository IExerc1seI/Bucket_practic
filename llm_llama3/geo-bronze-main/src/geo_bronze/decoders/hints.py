"""DecoderHint enum — contract with the silver layer.

Values must only be added, never edited or removed, to maintain backward compatibility.
"""
from enum import Enum


class DecoderHint(str, Enum):
    """Specifies how the silver layer should decode raw bronze bytes."""

    # GeoJSON / JSON-like
    GEOJSON = "geojson"
    """Standard GeoJSON FeatureCollection or Feature."""

    JSON_GENERIC = "json-generic"
    """Generic JSON without known spatial structure."""

    JSON_POINTS = "json-points"
    """JSON with top-level lat/lon fields per record."""

    JSON_WEATHER_FMI = "json-weather-fmi"
    """Meteo forecast JSON with dataTabs.latlon structure."""

    JS_WRAPPED_JSON = "js-wrapped-json"
    """JavaScript file containing `const X = {...};` wrapping JSON."""

    OVERPASS_JSON = "overpass-json"
    """JSON response from Overpass API with elements array."""

    # OGC / ArcGIS
    GML = "gml"
    """Geography Markup Language (OGC WFS response)."""

    ESRI_JSON = "esri-json"
    """Single ArcGIS JSON response (FeatureSet)."""

    ESRI_JSON_JSONL = "esri-json-jsonl"
    """Paginated ArcGIS responses merged as newline-delimited JSON."""

    # Tiles
    XYZ_RASTER_TILE = "xyz-raster-tile"
    """PNG/JPG/WebP XYZ raster tiles."""

    MVT = "mvt"
    """Mapbox Vector Tiles (.pbf format)."""

    # Rasters with or without georeferencing
    GEOTIFF = "geotiff"
    """GeoTIFF with embedded georeferencing."""

    PNG_WITH_BBOX_METADATA = "png-with-bbox-metadata"
    """PNG without embedded georef; bbox provided via sidecar bbox_override."""

    PNG_GEOREFERENCED_UNKNOWN = "png-georeferenced-unknown"
    """PNG whose georeferencing method is unknown; requires manual inspection."""

    # File formats
    SHAPEFILE_ZIP = "shapefile-zip"
    """ZIP archive containing an ESRI Shapefile."""

    OSM_PBF = "osm-pbf"
    """OpenStreetMap Protocol Buffer Format bulk extract."""

    CSV_LATLON = "csv-latlon"
    """CSV with explicit latitude/longitude columns."""

    CSV_GENERIC = "csv-generic"
    """Generic CSV without known spatial structure."""
