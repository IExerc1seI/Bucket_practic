"""Pydantic Settings configuration for geo-bronze."""
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM
    llm_provider: Literal["ollama"] = "llama3"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"

    # MinIO
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "geo-bronze"
    minio_secure: bool = False

    # Streaming
    streaming_threshold_bytes: int = 52_428_800  # 50 MB
    streaming_chunk_size_bytes: int = 5_242_880   # 5 MB

    # Logging
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    # Agent version (used in sidecars)
    agent_version: str = "0.1.0"


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return cached Settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
