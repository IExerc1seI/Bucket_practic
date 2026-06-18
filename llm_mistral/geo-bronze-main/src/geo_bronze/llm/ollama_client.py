"""Ollama local LLM client implementation."""
from __future__ import annotations

import httpx
import structlog

from geo_bronze.errors import LLMError

logger = structlog.get_logger(__name__)


class OllamaClient:
    """LLMClient implementation using the Ollama HTTP API."""

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "mistral") -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._log = logger.bind(component="OllamaClient", model=model)

    async def complete(
        self,
        system: str,
        user: str,
        response_schema: dict | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        """Send a completion request to the Ollama API using /api/generate."""

        prompt = f"{system}\n\n{user}"

        payload: dict = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        if response_schema is not None:
            payload["format"] = "json"

        self._log.debug("complete_request", model=self._model)

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{self._base_url}/api/generate",
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama HTTP error: {exc}") from exc

        data = response.json()

        content = data.get("response", "")

        self._log.debug("complete_response", length=len(content))
        return content