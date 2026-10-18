from abc import ABC, abstractmethod


class BaseYouTubeAggregator(ABC):
    """Abstract base class for YouTube video search providers."""

    @abstractmethod
    def search(self, query: str, max_results: int = 1) -> list[dict]:
        """
        Executes search query and returns list of video objects:
        [{"id": vid, "title": title, "channel": channel, "url": url}]
        """
        pass
