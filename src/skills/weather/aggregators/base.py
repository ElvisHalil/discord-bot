from abc import ABC, abstractmethod

class BaseWeatherAggregator(ABC):
    """Abstract base class for weather data providers."""

    @abstractmethod
    def get_weather(self, city: str) -> str:
        """Fetches current weather conditions and temperature for the given city."""
        pass

