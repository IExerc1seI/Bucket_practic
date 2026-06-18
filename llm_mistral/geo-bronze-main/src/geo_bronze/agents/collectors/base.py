"""BaseCollector — shared interface and utilities for all collectors."""
from __future__ import annotations

import asyncio
import hashlib
from abc import ABC, abstractmethod
from typing import ClassVar

import httpx
import structlog

from geo_bronze.errors import CollectorError
from geo_bronze.models.response import CollectorResponse
from geo_bronze.models.source import ProtocolFamily, Source
from geo_bronze.models.task import Subtask

logger = structlog.get_logger(__name__)

_DEFAULT_TIMEOUT = 30.0
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0


class BaseCollector(ABC):
    """Abstract base for all protocol-specific collectors."""

    protocol_family: ClassVar[ProtocolFamily]

    def __init__(self) -> None:
        self._log = logger.bind(collector=self.__class__.__name__, protocol=self.protocol_family)

    @abstractmethod
    async def collect(self, subtask: Subtask, source: Source) -> CollectorResponse:
        """Fetch data for a subtask from the given source.

        Args:
            subtask: The subtask to execute.
            source: The source configuration.

        Returns:
            CollectorResponse with raw_bytes or streamed_to_key set.
        """
        ...

    async def _fetch_with_retry(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        **kwargs: object,
    ) -> httpx.Response:
        """Execute an HTTP request with exponential backoff retry.

        Args:
            client: The httpx async client.
            method: HTTP method string.
            url: Target URL.
            **kwargs: Additional arguments forwarded to httpx.

        Returns:
            httpx.Response on success.

        Raises:
            CollectorError: After all retries are exhausted.
        """
        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = await client.request(method, url, **kwargs)  # type: ignore[arg-type]
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                # Don't retry 4xx client errors
                if exc.response.status_code < 500:
                    body_snippet = exc.response.text[:300].strip()
                    raise CollectorError(
                        f"HTTP {exc.response.status_code} for {url}: {body_snippet}"
                    ) from exc
                last_exc = exc
            except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
                last_exc = exc

            if attempt < _MAX_RETRIES:
                delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                self._log.warning("retry", attempt=attempt, delay=delay, url=url)
                await asyncio.sleep(delay)

        raise CollectorError(f"All {_MAX_RETRIES} retries failed for {url}: {last_exc}") from last_exc

    @staticmethod
    def _compute_hash(data: bytes) -> str:
        """Compute SHA-256 hex digest of bytes."""
        return hashlib.sha256(data).hexdigest()

    def _make_client(self, timeout: float = _DEFAULT_TIMEOUT) -> httpx.AsyncClient:
        """Create a configured httpx AsyncClient."""
        return httpx.AsyncClient(timeout=timeout, follow_redirects=True)
