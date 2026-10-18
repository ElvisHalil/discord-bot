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
from src.skills.weather.models import DailyForecast, GeocodedLocation, HourlyForecast
from src.utils.cache import BoundedCache
from src.utils.http import fetch_json

logger = logging.getLogger(__name__)

# Bounded in-memory cache of resolved city coordinates
_GEOCODE_CACHE: Dict[str, GeocodedLocation] = BoundedCache(maxsize=512)


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


def _safe_fetch_json(url: str, timeout: float = 8.0, retries: int = 1) -> Optional[dict]:
    """Fetch JSON from Open-Meteo with a fast retry on transient timeout/connection errors."""
    for attempt in range(retries + 1):
        try:
            data = fetch_json(url, timeout=timeout)
            if isinstance(data, dict):
                return data
            return None
        except Exception as exc:
            if attempt < retries:
                logger.debug("Open-Meteo request timed out/failed (attempt %d), retrying: %s", attempt + 1, exc)
                continue
            print(f"  └─ ⚠️ [Open-Meteo Notice]: Request failed ({exc}); falling back if available.")
            return None
    return None


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
        data = _safe_fetch_json(url, timeout=8)
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

    def get_weather(self, city: str, days: int = 7, hours: int = 12) -> str:
        """Fetch current weather conditions, hourly forecast (up to 48h), and multi-day forecast (1-16 days) for `city`."""
        try:
            loc = self.resolve_location(city)
            if not loc:
                return ""

            forecast_days = max(1, min(int(days or 7), 16))
            forecast_hours = max(0, min(int(hours if hours is not None else 12), 48))
            min_days_for_hours = (forecast_hours + 23) // 24 + 1 if forecast_hours > 0 else 1
            api_days = min(16, max(forecast_days, min_days_for_hours))

            params = (
                f"latitude={loc.latitude}&longitude={loc.longitude}"
                "&current=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,"
                "weather_code,wind_speed_10m,wind_direction_10m,wind_gusts_10m,uv_index"
                "&hourly=temperature_2m,apparent_temperature,precipitation_probability,precipitation,"
                "weather_code,wind_speed_10m,wind_direction_10m,uv_index"
                "&daily=weather_code,temperature_2m_max,temperature_2m_min,apparent_temperature_max,"
                "apparent_temperature_min,precipitation_sum,precipitation_probability_max,"
                "wind_speed_10m_max,uv_index_max,sunrise,sunset"
                f"&timezone=auto&forecast_days={api_days}"
            )
            data = _safe_fetch_json(f"{self.FORECAST_URL}?{params}", timeout=8)
            if not isinstance(data, dict) or "current" not in data:
                return ""

            return self._format_report(loc, data, forecast_days, forecast_hours=forecast_hours)
        except Exception as exc:
            logger.debug("Open-Meteo get_weather error for '%s': %s", city, exc)
            return ""

    @staticmethod
    def _format_report(loc: GeocodedLocation, data: dict, forecast_days: int, forecast_hours: int = 12) -> str:
        """Format Open-Meteo current conditions, hourly forecast, and daily forecast into a clean LLM-ready report."""
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

        # Hourly forecast block (starting at the city's current local hour)
        hourly = data.get("hourly") or {}
        h_times = hourly.get("time") or []
        if forecast_hours > 0 and h_times:
            h_codes = hourly.get("weather_code") or []
            h_temps = hourly.get("temperature_2m") or []
            h_feels = hourly.get("apparent_temperature") or []
            h_pops = hourly.get("precipitation_probability") or []
            h_precips = hourly.get("precipitation") or []
            h_winds = hourly.get("wind_speed_10m") or []
            h_wdirs = hourly.get("wind_direction_10m") or []
            h_uvs = hourly.get("uv_index") or []

            curr_time_str = str(curr.get("time") or "")
            start_idx = 0
            if len(curr_time_str) >= 13:
                curr_hour_prefix = curr_time_str[:13]
                for idx_h, t_val in enumerate(h_times):
                    if str(t_val)[:13] >= curr_hour_prefix:
                        start_idx = idx_h
                        break

            end_idx = min(len(h_times), start_idx + forecast_hours)
            if end_idx > start_idx:
                lines.append(f"\n=== NEXT {end_idx - start_idx}-HOUR FORECAST ===")
                for idx_h in range(start_idx, end_idx):
                    t_raw = str(h_times[idx_h])
                    try:
                        dt_h = datetime.strptime(t_raw, "%Y-%m-%dT%H:%M")
                        hour_12 = dt_h.strftime("%I:%M %p").lstrip("0")
                        time_label = f"{dt_h.strftime('%a %H:%M')} ({hour_12})"
                    except ValueError:
                        time_label = t_raw

                    h_cond = _wmo_description(h_codes[idx_h] if idx_h < len(h_codes) else None)
                    h_tc = float(h_temps[idx_h]) if idx_h < len(h_temps) and h_temps[idx_h] is not None else 0.0
                    h_fc = float(h_feels[idx_h]) if idx_h < len(h_feels) and h_feels[idx_h] is not None else h_tc
                    h_pop = int(h_pops[idx_h]) if idx_h < len(h_pops) and h_pops[idx_h] is not None else 0
                    h_pmm = float(h_precips[idx_h]) if idx_h < len(h_precips) and h_precips[idx_h] is not None else 0.0
                    h_wk = float(h_winds[idx_h]) if idx_h < len(h_winds) and h_winds[idx_h] is not None else 0.0
                    h_wd = _deg_to_compass(h_wdirs[idx_h] if idx_h < len(h_wdirs) else None)
                    h_uv = float(h_uvs[idx_h]) if idx_h < len(h_uvs) and h_uvs[idx_h] is not None else 0.0

                    hf = HourlyForecast(
                        time=t_raw,
                        condition=h_cond,
                        temp_c=h_tc,
                        feels_like_c=h_fc,
                        precip_prob_pct=h_pop,
                        precip_mm=h_pmm,
                        wind_kmh=h_wk,
                        wind_dir=h_wd,
                        uv_index=h_uv,
                    )
                    h_tf = _c_to_f(hf.temp_c)
                    h_wind_part = f"{hf.wind_kmh:.1f} km/h {hf.wind_dir}".strip()
                    lines.append(
                        f"- {time_label}: {hf.condition} | {hf.temp_c:.1f}°C ({h_tf:.1f}°F), "
                        f"Feels {hf.feels_like_c:.1f}°C | Precip: {hf.precip_prob_pct}% ({hf.precip_mm:.1f} mm) | "
                        f"Wind: {h_wind_part}"
                    )

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


