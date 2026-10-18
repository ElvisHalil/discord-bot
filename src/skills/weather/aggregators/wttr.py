import urllib.parse
from src.skills.weather.aggregators.base import BaseWeatherAggregator
from src.skills.weather.catalogs import WEATHER_DEFAULT_HEADERS, WEATHER_HTML_ERROR_PREFIXES
from src.skills.weather.enums import WeatherFormat
from src.utils.http import fetch_text


class WttrAggregator(BaseWeatherAggregator):
    """Weather aggregator using wttr.in service via centralized stealth client."""

    def get_weather(self, city: str) -> str:
        try:
            encoded_city = urllib.parse.quote((city or "").strip())
            text = fetch_text(
                f"https://wttr.in/{encoded_city}?format={WeatherFormat.COMPACT}",
                headers=WEATHER_DEFAULT_HEADERS,
                timeout=5,
            )
            if text and text.strip():
                cleaned = text.strip()
                low_cleaned = cleaned.lower()
                if any(p in low_cleaned for p in WEATHER_HTML_ERROR_PREFIXES):
                    return "Weather service temporarily unavailable."
                return cleaned
            return "Failed to retrieve weather data."
        except Exception as e:
            return f"Error fetching weather: {e}"
