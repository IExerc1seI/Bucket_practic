"""ArcGISCollector — handles ArcGIS FeatureServer/MapServer query endpoints."""
from __future__ import annotations

import json
from typing import ClassVar

import structlog

from geo_bronze.agents.collectors.base import BaseCollector
from geo_bronze.decoders.hints import DecoderHint
from geo_bronze.errors import CollectorError
from geo_bronze.models.response import CollectorResponse
from geo_bronze.models.source import ProtocolFamily, Source
from geo_bronze.models.task import ArcGISSubtaskParams, Subtask

logger = structlog.get_logger(__name__)

_PAGE_SIZE = 1000
_MAX_PAGES = 100


class ArcGISCollector(BaseCollector):
    """Collects data from ArcGIS FeatureServer/MapServer with automatic pagination."""

    protocol_family: ClassVar[ProtocolFamily] = "arcgis"

    async def collect(self, subtask: Subtask, source: Source) -> CollectorResponse:
        """Collect all features via paginated ArcGIS query endpoint.

        Merges all pages into a JSONL response with decoder_hint=ESRI_JSON_JSONL.
        """
        self._log.info("collect_start", subtask_id=subtask.subtask_id, source=source.source_id)
        assert isinstance(subtask.params, ArcGISSubtaskParams), "ArcGISCollector requires ArcGISSubtaskParams"
        params = subtask.params

        base_url = source.endpoint.rstrip("/") + "/" + params.service_path.lstrip("/")
        query_url = base_url.rstrip("/") + "/query"

        base_params: dict[str, str | int] = {
            "f": "json",
            "where": params.where,
            "outFields": params.out_fields,
            "outSR": params.out_sr,
            "returnGeometry": "true",
            "resultRecordCount": _PAGE_SIZE,
        }
        if params.geometry:
            base_params["geometry"] = json.dumps({
                "xmin": params.geometry[0],
                "ymin": params.geometry[1],
                "xmax": params.geometry[2],
                "ymax": params.geometry[3],
                "spatialReference": {"wkid": 4326},
            })
            base_params["geometryType"] = "esriGeometryEnvelope"
            base_params["inSR"] = "4326"

        pages: list[bytes] = []
        offset = 0
        total_features = 0

        try:
            async with self._make_client(timeout=60.0) as client:
                for page_num in range(_MAX_PAGES):
                    page_params = dict(base_params)
                    page_params["resultOffset"] = offset

                    response = await self._fetch_with_retry(
                        client, "GET", query_url, params=page_params
                    )
                    page_data = response.json()
                    features = page_data.get("features", [])
                    total_features += len(features)

                    if features:
                        pages.append(response.content)

                    exceeded = page_data.get("exceededTransferLimit", False)
                    if not exceeded or len(features) < _PAGE_SIZE:
                        break

                    offset += _PAGE_SIZE
                    if page_num == _MAX_PAGES - 1:
                        self._log.warning(
                            "pagination_limit_reached",
                            subtask_id=subtask.subtask_id,
                            max_pages=_MAX_PAGES,
                        )
        except CollectorError:
            raise
        except Exception as exc:
            return CollectorResponse(
                subtask_id=subtask.subtask_id, success=False, error=str(exc)
            )

        jsonl = b"\n".join(pages)
        content_hash = self._compute_hash(jsonl)

        self._log.info(
            "collect_complete",
            subtask_id=subtask.subtask_id,
            pages=len(pages),
            total_features=total_features,
        )
        return CollectorResponse(
            subtask_id=subtask.subtask_id,
            success=True,
            raw_bytes=jsonl,
            content_type="application/json",
            content_length=len(jsonl),
            content_hash=content_hash,
            source_metadata={
                "url": query_url,
                "total_features": total_features,
                "pages": len(pages),
            },
            decoder_hint=DecoderHint.ESRI_JSON_JSONL,
        )
