"""
Weather Aggregator Manager: Orchestrates Open-Meteo (Primary) with wttr.in (Fallback)
and provides a 10-minute TTL cache.
"""

from __future__ import annotations

import time
from typing import Dict, Tuple

from src.skills.weather.aggregators.open_meteo import OpenMeteoAggregator, clear_geocode_cache
from src.skills.weather.aggregators.wttr import WttrAggregator

_WEATHER_CACHE: Dict[Tuple[str, int], Tuple[float, str]] = {}
WEATHER_CACHE_TTL_SECONDS = 600


def clear_weather_cache() -> None:
    """Clear both the weather forecast TTL cache and geocoding cache."""
    _WEATHER_CACHE.clear()
    clear_geocode_cache()


class WeatherAggregatorManager:
    """Manages primary (Open-Meteo) and fallback (wttr.in) weather aggregators."""

    def __init__(self) -> None:
        self.open_meteo = OpenMeteoAggregator()
        self.wttr = WttrAggregator()

    def get_weather(self, city: str, days: int = 7) -> str:
        """Fetch current conditions and multi-day forecast using Open-Meteo with wttr.in fallback."""
        clean_city = (city or "").strip()
        if not clean_city:
            return "Please specify a city name."

        forecast_days = max(1, min(int(days or 7), 16))
        cache_key = (clean_city.lower(), forecast_days)
        now = time.time()

        cached = _WEATHER_CACHE.get(cache_key)
        if cached and (now - cached[0]) < WEATHER_CACHE_TTL_SECONDS:
            return cached[1]

        # 1. Primary: Open-Meteo (Current + up to 16-Day Forecast)
        report = self.open_meteo.get_weather(clean_city, days=forecast_days)
        if report and report.strip():
            _WEATHER_CACHE[cache_key] = (now, report)
            return report

        # 2. Fallback: wttr.in (3-Day JSON Forecast -> Compact 1-Liner)
        fallback = self.wttr.get_forecast(clean_city, days=min(forecast_days, 3))
        if (
            fallback
            and not fallback.startswith("Error")
            and "temporarily unavailable" not in fallback.lower()
            and "failed to retrieve" not in fallback.lower()
        ):
            _WEATHER_CACHE[cache_key] = (now, fallback)
        return fallback

