from src.skills.weather.aggregators.base import BaseWeatherAggregator
from src.utils.http import fetch_text

class WttrAggregator(BaseWeatherAggregator):
    """Weather aggregator using wttr.in service via centralized stealth client."""

    def get_weather(self, city: str) -> str:
        try:
            text = fetch_text(f"https://wttr.in/{city}?format=%C+%t+%w+%h", timeout=5)
            if text and text.strip():
                return text.strip()
            return "Failed to retrieve weather data."
        except Exception as e:
            return f"Error fetching weather: {e}"
