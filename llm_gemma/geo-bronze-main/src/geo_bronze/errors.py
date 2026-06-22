"""Custom exception classes for geo-bronze."""


class GeoBronzeError(Exception):
    """Base exception for all geo-bronze errors."""


class CollectorError(GeoBronzeError):
    """Raised when a collector fails to retrieve data."""


class ValidationError(GeoBronzeError):
    """Raised when validation of collected data fails."""


class BronzeWriteError(GeoBronzeError):
    """Raised when writing to bronze storage fails."""


class ConfigurationError(GeoBronzeError):
    """Raised when configuration is invalid or inconsistent."""


class StreamingError(GeoBronzeError):
    """Raised when a streaming upload fails."""


class LLMError(GeoBronzeError):
    """Raised when LLM interaction fails."""


class RegistryError(GeoBronzeError):
    """Raised when source registry operations fail."""
