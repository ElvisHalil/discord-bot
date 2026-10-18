from abc import ABC, abstractmethod
from typing import Optional


class BaseNewsAggregator(ABC):
    """Abstract base class for news headline aggregators."""

    @abstractmethod
    def get_headlines(self, region: Optional[str] = None, topic: Optional[str] = None) -> str:
        """Fetch and format top news headlines for a region/topic."""
        raise NotImplementedError

