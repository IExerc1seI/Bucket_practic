"""Protocol-specific data collectors."""
from geo_bronze.agents.collectors.arcgis import ArcGISCollector
from geo_bronze.agents.collectors.base import BaseCollector
from geo_bronze.agents.collectors.http import HTTPCollector
from geo_bronze.agents.collectors.ogc import OGCCollector
from geo_bronze.agents.collectors.tile import TileCollector

__all__ = ["ArcGISCollector", "BaseCollector", "HTTPCollector", "OGCCollector", "TileCollector"]
