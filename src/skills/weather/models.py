"""
Typed dataclass models for the Weather domain skill.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.utils.domain_vocab import DictLikeMixin


@dataclass(slots=True)
class GeocodedLocation(DictLikeMixin):
    """Resolved geographic coordinates and metadata for a city."""
    name: str
    latitude: float
    longitude: float
    country: str = ""
    admin1: str = ""
    timezone: str = "auto"


@dataclass(slots=True)
class HourlyForecast(DictLikeMixin):
    """Single-hour weather forecast summary."""
    time: str
    condition: str
    temp_c: float
    feels_like_c: float
    precip_prob_pct: int = 0
    precip_mm: float = 0.0
    wind_kmh: float = 0.0
    wind_dir: str = ""
    uv_index: float = 0.0


@dataclass(slots=True)
class DailyForecast(DictLikeMixin):
    """Single-day weather forecast summary."""
    date: str
    condition: str
    temp_max_c: float
    temp_min_c: float
    precip_prob_pct: int = 0
    precip_mm: float = 0.0
    wind_max_kmh: float = 0.0
    uv_index_max: float = 0.0
    sunrise: str = ""
    sunset: str = ""


@dataclass(slots=True)
class WeatherSnapshot(DictLikeMixin):
    """Normalized weather report snapshot for a city."""
    city: str
    conditions: str
    provider: str = "open_meteo"

