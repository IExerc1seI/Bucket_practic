"""LLM client factory."""
from __future__ import annotations

from geo_bronze.config import Settings
from geo_bronze.errors import ConfigurationError
from geo_bronze.llm.base import LLMClient


def get_llm_client(settings: Settings) -> LLMClient:
    """Return Ollama LLM client (Mistral only)."""
    
    from geo_bronze.llm.ollama_client import OllamaClient

    return OllamaClient(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
    )