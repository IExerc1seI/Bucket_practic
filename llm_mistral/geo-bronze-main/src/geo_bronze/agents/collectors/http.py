"""HTTPCollector — handles generic HTTP/HTTPS data sources."""
from __future__ import annotations

import hashlib
import json
import os
from typing import ClassVar

import httpx
import structlog
import urllib.parse

from geo_bronze.agents.collectors.base import BaseCollector, _DEFAULT_TIMEOUT
from geo_bronze.config import Settings, get_settings
from geo_bronze.decoders.hints import DecoderHint
from geo_bronze.errors import CollectorError, StreamingError
from geo_bronze.models.response import CollectorResponse
from geo_bronze.models.source import ProtocolFamily, Source
from geo_bronze.models.task import HTTPSubtaskParams, Subtask
from geo_bronze.storage.bronze import BronzeWriter

logger = structlog.get_logger(__name__)


class HTTPCollector(BaseCollector):
    """Collects data over HTTP/HTTPS, supporting GET/POST, templating, and streaming."""

    protocol_family: ClassVar[ProtocolFamily] = "http"

    def __init__(self, bronze_writer: BronzeWriter, settings: Settings | None = None) -> None:
        super().__init__()
        self._writer = bronze_writer
        self._settings = settings or get_settings()

    async def collect(self, subtask: Subtask, source: Source) -> CollectorResponse:
        """Collect data via HTTP.

        Automatically switches to streaming mode for large files.
        """
        self._log.info("collect_start", subtask_id=subtask.subtask_id, source=source.source_id)
        assert isinstance(subtask.params, HTTPSubtaskParams), "HTTPCollector requires HTTPSubtaskParams"
        params = subtask.params

        url = self._build_url(source, params)
        headers = self._build_headers(source, params)
        body, content_type_req = self._build_body(source, params)

        if content_type_req:
            headers.setdefault("Content-Type", content_type_req)
        headers.setdefault("User-Agent", f"geo-bronze/{self._settings.agent_version}")

        try:
            async with self._make_client() as client:
                if params.force_streaming:
                    return await self._streaming_collect(
                        client, subtask, source, params, url, headers, body
                    )

                # HEAD request to check content-length (GET only — POST endpoints don't support it)
                cl = 0
                if params.method == "GET":
                    try:
                        head_resp = await client.head(url, headers=headers, timeout=10.0)
                        cl = int(head_resp.headers.get("content-length", "0"))
                    except Exception:
                        cl = 0

                is_streaming_source = source.metadata.get("streaming", False)
                threshold = self._settings.streaming_threshold_bytes

                if is_streaming_source or (cl and cl >= threshold):
                    return await self._streaming_collect(
                        client, subtask, source, params, url, headers, body
                    )
                else:
                    return await self._normal_collect(
                        client, subtask, source, params, url, headers, body
                    )
        except (CollectorError, StreamingError):
            raise
        except Exception as exc:
            self._log.error("collect_error", subtask_id=subtask.subtask_id, error=str(exc))
            return CollectorResponse(
                subtask_id=subtask.subtask_id,
                success=False,
                error=str(exc),
            )


    def _build_url(self, source: Source, params: HTTPSubtaskParams) -> str:
        base = source.endpoint.rstrip("/")
        path = params.path or ""

        # ✅ FIX: гарантируем, что path начинается с "/"
        if path and not path.startswith("/"):
            path = "/" + path

        url = base + path

        # ✅ FIX: DSNS требует trailing slash
        if "mine.dsns.gov.ua" in url and not url.endswith("/"):
            url += "/"

        return url



    def _build_headers(self, source: Source, params: HTTPSubtaskParams) -> dict[str, str]:
        """Merge default and subtask-level headers."""
        merged = dict(source.metadata.get("default_headers", {}))
        merged.update(params.headers)
        merged.setdefault("Accept", "application/json")
        return merged

    def _build_body(self, source: Source, params: HTTPSubtaskParams) -> tuple[bytes | None, str | None]:
        """Build the request body and determine Content-Type."""
        if params.body is not None:
            if isinstance(params.body, dict):
                return json.dumps(params.body).encode("utf-8"), "application/json"
            if isinstance(params.body, str):
                return params.body.encode("utf-8"), "text/plain"
            if isinstance(params.body, bytes):
                return params.body, None
        if params.body_template_vars is not None:
            template = source.metadata.get("body_template", "")
            if template:
                rendered = template.format(**params.body_template_vars)
                form_field = source.metadata.get("body_form_field")
                logger.info("rendered_body_template", template=template, vars=params.body_template_vars, rendered=rendered, form_field=form_field)
                if form_field:
                    from urllib.parse import urlencode
                    return urlencode({form_field: rendered}).encode("utf-8"), "application/x-www-form-urlencoded"
                return rendered, "text/plain"
        return None, None

    def _build_query_params(self, source: Source, params: HTTPSubtaskParams) -> dict[str, str]:
        """Merge source query_template and subtask query_params (subtask has priority)."""
        merged = dict(source.metadata.get("query_template", {}))
        merged.update(params.query_params)
        return merged

    async def _normal_collect(
        self,
        client: httpx.AsyncClient,
        subtask: Subtask,
        source: Source,
        params: HTTPSubtaskParams,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
    ) -> CollectorResponse:
        """Download data fully into memory."""
        query = self._build_query_params(source, params)
        logger.info("http_request", method=params.method, url=url, headers=headers, body=body)
        response = await self._fetch_with_retry(
            client,
            params.method,
            url,
            headers=headers,
            params=query if query else None,
            content=body,
        )

        raw = response.content
        content_type = response.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
        
        if content_type.startswith("text/html") or raw[:50].lower().startswith(b"<!doctype html"):
            raise CollectorError(f"Expected JSON but got HTML from {url}. Likely wrong endpoint")


        content_hash = self._compute_hash(raw)
        decoder_hint = self._determine_decoder_hint(url, content_type, raw, params, source)

        self._log.info(
            "collect_complete",
            subtask_id=subtask.subtask_id,
            size=len(raw),
            content_type=content_type,
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

    async def _streaming_collect(
        self,
        client: httpx.AsyncClient,
        subtask: Subtask,
        source: Source,
        params: HTTPSubtaskParams,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
    ) -> CollectorResponse:
        """Download large files via multipart upload to bronze storage."""
        from datetime import datetime, timezone

        query = self._build_query_params(source, params)
        key = self._make_key(subtask, source, url)
        upload_id: str | None = None

        try:
            async with client.stream(
                params.method,
                url,
                headers=headers,
                params=query if query else None,
                content=body,
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
                upload_id = self._writer.stream_start(key, content_type)

                sha = hashlib.sha256()
                parts: list[tuple[int, str]] = []
                part_num = 1
                buffer = b""
                total = 0
                chunk_size = self._settings.streaming_chunk_size_bytes

                async for chunk in response.aiter_bytes(chunk_size=65536):
                    buffer += chunk
                    sha.update(chunk)
                    total += len(chunk)

                    if len(buffer) >= chunk_size:
                        etag = self._writer.stream_part(upload_id, key, part_num, buffer)
                        parts.append((part_num, etag))
                        part_num += 1
                        buffer = b""

                # Upload remaining bytes as final part
                if buffer:
                    etag = self._writer.stream_part(upload_id, key, part_num, buffer)
                    parts.append((part_num, etag))

            self._writer.stream_complete(upload_id, key, parts)
            content_hash = sha.hexdigest()
            decoder_hint = self._determine_decoder_hint(url, content_type, b"", params, source)

            self._log.info(
                "streaming_complete",
                subtask_id=subtask.subtask_id,
                key=key,
                total_bytes=total,
            )
            return CollectorResponse(
                subtask_id=subtask.subtask_id,
                success=True,
                raw_bytes=None,
                streamed_to_key=key,
                content_type=content_type,
                content_length=total,
                content_hash=content_hash,
                source_metadata={
                    "url": url,
                    "status_code": response.status_code,
                    "headers": dict(response.headers),
                },
                decoder_hint=decoder_hint,
            )

        except Exception as exc:
            if upload_id is not None:
                self._writer.stream_abort(upload_id, key)
            self._log.error("streaming_failed", subtask_id=subtask.subtask_id, error=str(exc))
            raise StreamingError(f"Streaming failed for {url}: {exc}") from exc

    def _make_key(self, subtask: Subtask, source: Source, url: str) -> str:
        """Generate a bronze storage key for streaming uploads."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        ext = self._url_extension(url) or "bin"
        return (
            f"{source.source_id}/{now.year}/{now.month:02d}/{now.day:02d}"
            f"/{subtask.parent_task_id}/{subtask.subtask_id}.{ext}"
        )

    @staticmethod
    def _url_extension(url: str) -> str | None:
        """Extract file extension from URL path."""
        path = url.split("?")[0]
        parts = path.rsplit(".", 1)
        return parts[-1].lower() if len(parts) == 2 and len(parts[-1]) <= 6 else None

    def _determine_decoder_hint(
        self,
        url: str,
        content_type: str,
        raw: bytes,
        params: HTTPSubtaskParams,
        source: Source,
    ) -> DecoderHint:
        """Determine DecoderHint with priority: explicit > source metadata > heuristics."""
        # Priority 1: explicit in subtask params
        if params.decoder_hint:
            try:
                return DecoderHint(params.decoder_hint)
            except ValueError:
                pass

        # Priority 2: source metadata decoder_hint
        meta_hint = source.metadata.get("decoder_hint")
        if meta_hint:
            try:
                return DecoderHint(meta_hint)
            except ValueError:
                pass

        # Priority 3: heuristics from Content-Type + URL extension
        ct = content_type.lower()
        url_lower = url.lower()

        if url_lower.endswith(".osm.pbf"):
            return DecoderHint.OSM_PBF
        if url_lower.endswith(".zip"):
            return DecoderHint.SHAPEFILE_ZIP
        if url_lower.endswith(".js"):
            return DecoderHint.JS_WRAPPED_JSON
        if url_lower.endswith(".geotiff") or url_lower.endswith(".tif"):
            return DecoderHint.GEOTIFF

        if "application/json" in ct or "text/json" in ct:
            if raw:
                snippet = raw[:512]
                if b'"FeatureCollection"' in snippet or b'"Feature"' in snippet:
                    return DecoderHint.GEOJSON
                if b'"elements"' in snippet and b'"version"' in snippet:
                    return DecoderHint.OVERPASS_JSON
            return DecoderHint.JSON_GENERIC

        if "image/png" in ct or "image/jpg" in ct or "image/jpeg" in ct or "image/webp" in ct:
            if source.metadata.get("bbox_override"):
                return DecoderHint.PNG_WITH_BBOX_METADATA
            return DecoderHint.PNG_GEOREFERENCED_UNKNOWN

        if "image/tiff" in ct or "image/geotiff" in ct:
            return DecoderHint.GEOTIFF

        return DecoderHint.JSON_GENERIC
