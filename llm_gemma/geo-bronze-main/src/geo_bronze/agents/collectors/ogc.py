"""OGCCollector — handles WMS, WFS, WCS, CSW sources (MVP: WFS GetFeature only)."""
from __future__ import annotations

from typing import ClassVar

import structlog

from geo_bronze.agents.collectors.base import BaseCollector
from geo_bronze.decoders.hints import DecoderHint
from geo_bronze.errors import CollectorError
from geo_bronze.models.response import CollectorResponse
from geo_bronze.models.source import ProtocolFamily, Source
from geo_bronze.models.task import OGCSubtaskParams, Subtask

logger = structlog.get_logger(__name__)


class OGCCollector(BaseCollector):
    """Collects data from OGC-compliant services (WFS, WMS, WCS, CSW).

    MVP scope: WFS GetFeature only. Other operations raise NotImplementedError.
    """

    protocol_family: ClassVar[ProtocolFamily] = "ogc"

    async def collect(self, subtask: Subtask, source: Source) -> CollectorResponse:
        """Collect data from an OGC service endpoint."""
        self._log.info("collect_start", subtask_id=subtask.subtask_id, source=source.source_id)
        assert isinstance(subtask.params, OGCSubtaskParams), "OGCCollector requires OGCSubtaskParams"
        params = subtask.params

        if params.service != "WFS":
            raise NotImplementedError(
                f"OGCCollector MVP only supports WFS. Got service='{params.service}'. "
                f"WMS/WCS/CSW support is planned for a future milestone."
            )
        if params.operation != "GetFeature":
            raise NotImplementedError(
                f"OGCCollector MVP only supports GetFeature. Got operation='{params.operation}'."
            )

        return await self._wfs_get_feature(subtask, source, params)

    async def _wfs_get_feature(
        self, subtask: Subtask, source: Source, params: OGCSubtaskParams
    ) -> CollectorResponse:
        """Execute WFS GetFeature request."""
        query: dict[str, str] = {
            "SERVICE": "WFS",
            "VERSION": params.version,
            "REQUEST": "GetFeature",
            "outputFormat": params.output_format,
        }
        if params.type_name:
            query["TYPENAMES"] = params.type_name
        if params.bbox:
            query["BBOX"] = ",".join(str(c) for c in params.bbox)

        url = source.endpoint
        try:
            async with self._make_client() as client:
                response = await self._fetch_with_retry(client, "GET", url, params=query)
        except CollectorError:
            raise
        except Exception as exc:
            return CollectorResponse(
                subtask_id=subtask.subtask_id, success=False, error=str(exc)
            )

        raw = response.content
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        decoder_hint = self._resolve_decoder_hint(content_type)
        content_hash = self._compute_hash(raw)

        self._log.info(
            "collect_complete",
            subtask_id=subtask.subtask_id,
            size=len(raw),
            decoder_hint=decoder_hint.value,
        )
        return CollectorResponse(
            subtask_id=subtask.subtask_id,
            success=True,
            raw_bytes=raw,
            content_type=content_type,
            content_length=len(raw),
            content_hash=content_hash,
            source_metadata={
                "url": str(response.url),
                "status_code": response.status_code,
                "headers": dict(response.headers),
            },
            decoder_hint=decoder_hint,
        )

    @staticmethod
    def _resolve_decoder_hint(content_type: str) -> DecoderHint:
        """Map WFS response Content-Type to DecoderHint."""
        if "application/json" in content_type or "json" in content_type:
            return DecoderHint.GEOJSON
        if "gml" in content_type or "xml" in content_type:
            return DecoderHint.GML
        return DecoderHint.GML  # WFS default is GML
