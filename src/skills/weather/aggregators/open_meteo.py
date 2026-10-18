"""
Primary Weather & Multi-Day Forecast Aggregator powered by Open-Meteo.
Free, keyless API blending high-resolution national weather models (NOAA, ECMWF,
Environment Canada, DWD ICON, JMA) with up to 16 days of daily forecasts.
"""

from __future__ import annotations

from datetime import datetime
import logging
import urllib.parse
from typing import Dict, List, Optional

from src.skills.weather.aggregators.base import BaseWeatherAggregator
from src.skills.weather.catalogs import COMPASS_DIRECTIONS, WMO_WEATHER_CODES
from src.skills.weather.models import DailyForecast, GeocodedLocation
from src.utils.http import fetch_json

logger = logging.getLogger(__name__)

# Permanent in-memory cache of resolved city coordinates (city coordinates do not change)
_GEOCODE_CACHE: Dict[str, GeocodedLocation] = {}


def clear_geocode_cache() -> None:
    """Clear the in-memory city geocoding cache."""
    _GEOCODE_CACHE.clear()


def _c_to_f(temp_c: float) -> float:
    """Convert Celsius to Fahrenheit."""
    return (temp_c * 9.0 / 5.0) + 32.0


def _deg_to_compass(degrees: Optional[float]) -> str:
    """Convert wind direction in degrees (0-360) to a 16-point compass cardinal direction."""
    if degrees is None:
        return ""
    idx = int(round(float(degrees) / 22.5)) % 16
    return COMPASS_DIRECTIONS[idx]


def _wmo_description(code: Optional[int]) -> str:
    """Translate a WMO weather interpretation code into a human-readable condition string."""
    if code is None:
        return "Unknown"
    return WMO_WEATHER_CODES.get(int(code), f"Weather code {code}")


class OpenMeteoAggregator(BaseWeatherAggregator):
    """Primary weather & forecast provider using Open-Meteo Geocoding and Forecast APIs."""

    GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
    FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

    def resolve_location(self, city_query: str) -> Optional[GeocodedLocation]:
        """Resolve a city query (e.g. 'Paris, France' or 'Tokyo') into geographic coordinates."""
        raw = (city_query or "").strip()
        if not raw:
            return None

        cache_key = raw.lower()
        if cache_key in _GEOCODE_CACHE:
            return _GEOCODE_CACHE[cache_key]

        # Split optional 'City, Region/Country' qualifier so we can filter top geocoding results
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        search_name = parts[0]
        qualifiers = [p.lower() for p in parts[1:]]

        encoded_name = urllib.parse.quote(search_name)
        url = f"{self.GEOCODING_URL}?name={encoded_name}&count=5&language=en&format=json"
        data = fetch_json(url, timeout=5)
        if not isinstance(data, dict) or not data.get("results"):
            return None

        results = data["results"]
        chosen = results[0]
        if qualifiers:
            for candidate in results:
                haystack = " ".join(
                    str(candidate.get(k, "")).lower()
                    for k in ("admin1", "admin2", "country", "country_code")
                )
                if all(q in haystack for q in qualifiers):
                    chosen = candidate
                    break

        try:
            loc = GeocodedLocation(
                name=str(chosen.get("name") or search_name),
                latitude=float(chosen["latitude"]),
                longitude=float(chosen["longitude"]),
                country=str(chosen.get("country") or ""),
                admin1=str(chosen.get("admin1") or ""),
                timezone=str(chosen.get("timezone") or "auto"),
            )
            _GEOCODE_CACHE[cache_key] = loc
            return loc
        except (KeyError, TypeError, ValueError) as exc:
            logger.debug("Failed to parse Open-Meteo geocoding result for '%s': %s", raw, exc)
            return None

    def get_weather(self, city: str, days: int = 7) -> str:
        """Fetch current weather conditions and a multi-day forecast (1-16 days) for `city`."""
        loc = self.resolve_location(city)
        if not loc:
            return ""

        forecast_days = max(1, min(int(days or 7), 16))
        params = (
            f"latitude={loc.latitude}&longitude={loc.longitude}"
            "&current=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,"
            "weather_code,wind_speed_10m,wind_direction_10m,wind_gusts_10m,uv_index"
            "&daily=weather_code,temperature_2m_max,temperature_2m_min,apparent_temperature_max,"
            "apparent_temperature_min,precipitation_sum,precipitation_probability_max,"
            "wind_speed_10m_max,uv_index_max,sunrise,sunset"
            f"&timezone=auto&forecast_days={forecast_days}"
        )
        data = fetch_json(f"{self.FORECAST_URL}?{params}", timeout=6)
        if not isinstance(data, dict) or "current" not in data:
            return ""

        return self._format_report(loc, data, forecast_days)

    @staticmethod
    def _format_report(loc: GeocodedLocation, data: dict, forecast_days: int) -> str:
        """Format Open-Meteo current conditions and daily forecast into a clean LLM-ready report."""
        region_parts = [loc.name]
        if loc.admin1 and loc.admin1.lower() != loc.name.lower():
            region_parts.append(loc.admin1)
        if loc.country:
            region_parts.append(loc.country)
        location_label = ", ".join(region_parts)
        tz_label = data.get("timezone") or loc.timezone or "UTC"

        curr = data.get("current") or {}
        temp_c = float(curr.get("temperature_2m", 0.0))
        feels_c = float(curr.get("apparent_temperature", temp_c))
        temp_f = _c_to_f(temp_c)
        feels_f = _c_to_f(feels_c)
        humidity = curr.get("relative_humidity_2m", "N/A")
        cond_desc = _wmo_description(curr.get("weather_code"))
        wind_kmh = float(curr.get("wind_speed_10m", 0.0))
        wind_gusts = float(curr.get("wind_gusts_10m", 0.0))
        wind_dir = _deg_to_compass(curr.get("wind_direction_10m"))
        precip_mm = float(curr.get("precipitation", 0.0))
        uv_idx = curr.get("uv_index")

        wind_str = f"{wind_kmh:.1f} km/h {wind_dir}".strip()
        if wind_gusts > wind_kmh + 5.0:
            wind_str += f" (gusts {wind_gusts:.1f} km/h)"

        lines: List[str] = [
            f"Location: {location_label} (Timezone: {tz_label})",
            (
                f"Current: {cond_desc}, {temp_c:.1f}°C ({temp_f:.1f}°F) | "
                f"Feels Like: {feels_c:.1f}°C ({feels_f:.1f}°F) | "
                f"Humidity: {humidity}% | Wind: {wind_str} | "
                f"Precip: {precip_mm:.1f} mm"
                + (f" | UV Index: {float(uv_idx):.1f}" if uv_idx is not None else "")
            ),
        ]

        daily = data.get("daily") or {}
        dates = daily.get("time") or []
        codes = daily.get("weather_code") or []
        t_max_list = daily.get("temperature_2m_max") or []
        t_min_list = daily.get("temperature_2m_min") or []
        pop_list = daily.get("precipitation_probability_max") or []
        psum_list = daily.get("precipitation_sum") or []
        wmax_list = daily.get("wind_speed_10m_max") or []
        uv_list = daily.get("uv_index_max") or []
        sunrise_list = daily.get("sunrise") or []
        sunset_list = daily.get("sunset") or []

        if dates:
            lines.append(f"\n=== {min(len(dates), forecast_days)}-DAY FORECAST ===")
            for idx, d_str in enumerate(dates[:forecast_days]):
                try:
                    dt_obj = datetime.strptime(d_str, "%Y-%m-%d")
                    day_label = dt_obj.strftime("%a, %b %d")
                except ValueError:
                    day_label = d_str
                if idx == 0:
                    day_label = f"Today ({day_label})"
                elif idx == 1:
                    day_label = f"Tomorrow ({day_label})"

                d_cond = _wmo_description(codes[idx] if idx < len(codes) else None)
                hi_c = float(t_max_list[idx]) if idx < len(t_max_list) and t_max_list[idx] is not None else 0.0
                lo_c = float(t_min_list[idx]) if idx < len(t_min_list) and t_min_list[idx] is not None else 0.0
                pop = int(pop_list[idx]) if idx < len(pop_list) and pop_list[idx] is not None else 0
                psum = float(psum_list[idx]) if idx < len(psum_list) and psum_list[idx] is not None else 0.0
                wmax = float(wmax_list[idx]) if idx < len(wmax_list) and wmax_list[idx] is not None else 0.0
                uv_max = float(uv_list[idx]) if idx < len(uv_list) and uv_list[idx] is not None else 0.0
                sr = str(sunrise_list[idx]).split("T")[-1] if idx < len(sunrise_list) and sunrise_list[idx] else ""
                ss = str(sunset_list[idx]).split("T")[-1] if idx < len(sunset_list) and sunset_list[idx] else ""

                fc = DailyForecast(
                    date=d_str,
                    condition=d_cond,
                    temp_max_c=hi_c,
                    temp_min_c=lo_c,
                    precip_prob_pct=pop,
                    precip_mm=psum,
                    wind_max_kmh=wmax,
                    uv_index_max=uv_max,
                    sunrise=sr,
                    sunset=ss,
                )
                hi_f = _c_to_f(fc.temp_max_c)
                lo_f = _c_to_f(fc.temp_min_c)
                sun_part = f" | Sun: {fc.sunrise}–{fc.sunset}" if fc.sunrise and fc.sunset else ""
                lines.append(
                    f"- {day_label}: {fc.condition} | High: {fc.temp_max_c:.1f}°C ({hi_f:.1f}°F), "
                    f"Low: {fc.temp_min_c:.1f}°C ({lo_f:.1f}°F) | "
                    f"Precip Chance: {fc.precip_prob_pct}% ({fc.precip_mm:.1f} mm) | "
                    f"Max Wind: {fc.wind_max_kmh:.1f} km/h{sun_part}"
                )

        return "\n".join(lines)

