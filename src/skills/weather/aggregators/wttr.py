import requests
from src.skills.weather.aggregators.base import BaseWeatherAggregator

class WttrAggregator(BaseWeatherAggregator):
    """Weather aggregator using wttr.in service."""

    def get_weather(self, city: str) -> str:
        try:
            response = requests.get(f"https://wttr.in/{city}?format=%C+%t+%w+%h", timeout=5)
            if response.status_code == 200 and response.text.strip():
                return response.text.strip()
            return f"Failed to retrieve weather (HTTP {response.status_code})"
        except Exception as e:
            return f"Error fetching weather: {e}"
