"""Anthropic Claude API client implementation."""
from __future__ import annotations

import re

import structlog

from geo_bronze.errors import LLMError

logger = structlog.get_logger(__name__)


class AnthropicClient:
    """LLMClient implementation using the Anthropic SDK."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-5") -> None:
        try:
            import anthropic

            self._client = anthropic.AsyncAnthropic(api_key=api_key)
        except ImportError as exc:
            raise LLMError("anthropic package is not installed") from exc
        self._model = model
        self._log = logger.bind(component="AnthropicClient", model=model)

    async def complete(
        self,
        system: str,
        user: str,
        response_schema: dict | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        """Send a completion request to the Anthropic API.

        Instructs the model to return structured output inside <output>...</output> tags
        when response_schema is provided.
        """
        import anthropic

        effective_system = system
        if response_schema is not None:
            effective_system = (
                f"{system}\n\nReturn your answer as valid JSON inside <output>...</output> tags."
            )

        self._log.debug("complete_request", model=self._model, max_tokens=max_tokens)
        try:
            message = await self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=effective_system,
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.APIError as exc:
            raise LLMError(f"Anthropic API error: {exc}") from exc

        raw = message.content[0].text if message.content else ""
        self._log.debug("complete_response", tokens=message.usage.output_tokens if message.usage else 0)

        if response_schema is not None:
            return self._extract_output_tag(raw)
        return raw

    @staticmethod
    def _extract_output_tag(text: str) -> str:
        """Extract JSON content from <output>...</output> tags."""
        match = re.search(r"<output>(.*?)</output>", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        # Fallback: try to find a JSON object/array directly
        match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return text
