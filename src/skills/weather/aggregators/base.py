from abc import ABC, abstractmethod


class BaseWeatherAggregator(ABC):
    """Abstract base class for weather data providers."""

    @abstractmethod
    def get_weather(self, city: str, days: int = 7) -> str:
        """Fetches current weather conditions and multi-day forecast for the given city."""
        pass
