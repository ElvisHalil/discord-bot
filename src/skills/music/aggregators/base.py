from abc import ABC, abstractmethod
from src.skills.music.models import Track

class BaseMusicAggregator(ABC):
    """Abstract base class for all music data and recommendation providers."""

    @abstractmethod
    def get_similar(self, query: str) -> list[Track]:
        """
        Finds songs similar to the provided track/artist query.
        Returns a list of Track models.
        """
        pass

    @abstractmethod
    def get_charts(self, genre: str = None) -> list[Track]:
        """
        Fetches top charting or trending music tracks.
        Returns a list of Track models.
        """
        pass
