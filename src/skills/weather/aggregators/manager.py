"""
Weather Aggregator Manager: Orchestrates Open-Meteo (Primary) with wttr.in (Fallback)
and provides a 10-minute TTL cache.
"""

from __future__ import annotations

import re
import time
from typing import Dict, List, Tuple

from src.skills.weather.aggregators.open_meteo import OpenMeteoAggregator, clear_geocode_cache
from src.skills.weather.aggregators.wttr import WttrAggregator
from src.utils.workers import map_in_workers

_WEATHER_CACHE: Dict[Tuple[str, int, int], Tuple[float, str]] = {}
WEATHER_CACHE_TTL_SECONDS = 600
_MULTI_CITY_SPLIT_RE = re.compile(r"\s*(?:;|\band\b|\bvs\.?\b|\bversus\b|&|/)\s*", re.IGNORECASE)


def clear_weather_cache() -> None:
    """Clear both the weather forecast TTL cache and geocoding cache."""
    _WEATHER_CACHE.clear()
    clear_geocode_cache()


class WeatherAggregatorManager:
    """Manages primary (Open-Meteo) and fallback (wttr.in) weather aggregators."""

    def __init__(self) -> None:
        self.open_meteo = OpenMeteoAggregator()
        self.wttr = WttrAggregator()

    @staticmethod
    def _split_cities(city_query: str) -> List[str]:
        """Split multi-city queries (e.g. 'Tokyo and London' or 'Paris, France; Berlin, Germany')
        while preserving comma-separated region/country qualifiers like 'Paris, France'."""
        parts = [p.strip() for p in _MULTI_CITY_SPLIT_RE.split(city_query) if p and p.strip()]
        seen = set()
        unique_cities: List[str] = []
        for p in parts:
            key = p.lower()
            if key not in seen:
                seen.add(key)
                unique_cities.append(p)
        return unique_cities[:4]

    def _get_single_city_weather(self, clean_city: str, forecast_days: int, forecast_hours: int = 12) -> str:
        cache_key = (clean_city.lower(), forecast_days, forecast_hours)
        now = time.time()

        cached = _WEATHER_CACHE.get(cache_key)
        if cached and (now - cached[0]) < WEATHER_CACHE_TTL_SECONDS:
            return cached[1]

        # 1. Primary: Open-Meteo (Current + Hourly + up to 16-Day Forecast)
        try:
            if forecast_hours == 12:
                report = self.open_meteo.get_weather(clean_city, days=forecast_days)
            else:
                report = self.open_meteo.get_weather(clean_city, days=forecast_days, hours=forecast_hours)
        except Exception as exc:
            print(f"  └─ ⚠️ [Weather Manager]: Primary provider failed for '{clean_city}' ({exc}); using wttr.in fallback.")
            report = ""

        if report and report.strip():
            _WEATHER_CACHE[cache_key] = (now, report)
            return report

        # 2. Fallback: wttr.in (3-Day JSON Forecast with 3-hr slots -> Compact 1-Liner)
        if forecast_hours == 12:
            fallback = self.wttr.get_forecast(clean_city, days=min(forecast_days, 3))
        else:
            fallback = self.wttr.get_forecast(clean_city, days=min(forecast_days, 3), hours=forecast_hours)
        if (
            fallback
            and not fallback.startswith("Error")
            and "temporarily unavailable" not in fallback.lower()
            and "failed to retrieve" not in fallback.lower()
        ):
            _WEATHER_CACHE[cache_key] = (now, fallback)
        return fallback

    def get_weather(self, city: str, days: int = 7, hours: int = 12) -> str:
        """Fetch current conditions, hourly forecast, and multi-day forecast using Open-Meteo with wttr.in fallback.
        Automatically fans out across SUB_WORKER_POOL when multiple cities are requested."""
        clean_city = (city or "").strip()
        if not clean_city:
            return "Please specify a city name."

        forecast_days = max(1, min(int(days or 7), 16))
        forecast_hours = max(0, min(int(hours if hours is not None else 12), 48))
        cities = self._split_cities(clean_city)
        reports = map_in_workers(
            lambda c: self._get_single_city_weather(c, forecast_days, forecast_hours),
            cities,
        )
        return "\n\n=============================================\n\n".join(r for r in reports if r)



