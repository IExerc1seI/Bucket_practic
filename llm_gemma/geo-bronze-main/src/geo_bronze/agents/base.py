"""BaseAgent — shared interface for all agents."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

import structlog

from geo_bronze.llm.base import LLMClient


class BaseAgent(ABC):
    """Abstract base class for all agents in the geo-bronze system."""

    name: ClassVar[str] = "BaseAgent"
    version: ClassVar[str] = "0.1.0"

    def __init__(self, llm: LLMClient | None = None) -> None:
        self._llm = llm
        self._log = structlog.get_logger(__name__).bind(
            agent=self.name,
            agent_version=self.version,
        )

    @abstractmethod
    async def run(self, *args: Any, **kwargs: Any) -> Any:
        """Main execution method. Must be implemented by each agent."""
        ...
