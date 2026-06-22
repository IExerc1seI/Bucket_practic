"""LLMClient Protocol — abstract interface for LLM backends."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """Protocol that all LLM client implementations must satisfy."""

    async def complete(
        self,
        system: str,
        user: str,
        response_schema: dict | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        """Send a chat completion request and return the text response.

        Args:
            system: System prompt.
            user: User message.
            response_schema: Optional JSON Schema for structured output.
            temperature: Sampling temperature (0.0 = deterministic).
            max_tokens: Maximum output tokens.

        Returns:
            The model's text response.
        """
        ...
