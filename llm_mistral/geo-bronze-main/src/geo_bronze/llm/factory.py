"""LLM client factory."""
from __future__ import annotations

from geo_bronze.config import Settings
from geo_bronze.errors import ConfigurationError
from geo_bronze.llm.base import LLMClient


def get_llm_client(settings: Settings) -> LLMClient:
    """Instantiate the appropriate LLM client based on settings.llm_provider.

    Args:
        settings: Application settings.

    Returns:
        An LLMClient instance (either AnthropicClient or OllamaClient).
    """
    if settings.llm_provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ConfigurationError("ANTHROPIC_API_KEY must be set when llm_provider=anthropic")
        from geo_bronze.llm.anthropic_client import AnthropicClient

        return AnthropicClient(api_key=settings.anthropic_api_key, model=settings.anthropic_model)
    elif settings.llm_provider == "ollama":
        from geo_bronze.llm.ollama_client import OllamaClient

        return OllamaClient(base_url=settings.ollama_base_url, model=settings.ollama_model)
    else:
        raise ConfigurationError(f"Unknown llm_provider: {settings.llm_provider}")
