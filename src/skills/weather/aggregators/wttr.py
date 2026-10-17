import urllib.parse
from src.skills.weather.aggregators.base import BaseWeatherAggregator
from src.utils.http import fetch_text

class WttrAggregator(BaseWeatherAggregator):
    """Weather aggregator using wttr.in service via centralized stealth client."""

    def get_weather(self, city: str) -> str:
        try:
            encoded_city = urllib.parse.quote((city or "").strip())
            text = fetch_text(
                f"https://wttr.in/{encoded_city}?format=%C+%t+%w+%h",
                headers={"User-Agent": "curl/8.0"},
                timeout=5,
            )
            if text and text.strip():
                cleaned = text.strip()
                if cleaned.lower().startswith("<!doctype") or "<html" in cleaned.lower():
                    return "Weather service temporarily unavailable."
                return cleaned
            return "Failed to retrieve weather data."
        except Exception as e:
            return f"Error fetching weather: {e}"
