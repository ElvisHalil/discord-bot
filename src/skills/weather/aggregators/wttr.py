"""
Fallback Weather Aggregator using wttr.in (supports both 3-day JSON and compact text fallback).
"""

from __future__ import annotations

import urllib.parse
from typing import List

from src.skills.weather.aggregators.base import BaseWeatherAggregator
from src.skills.weather.catalogs import WEATHER_DEFAULT_HEADERS, WEATHER_HTML_ERROR_PREFIXES
from src.skills.weather.enums import WeatherFormat
from src.utils.http import fetch_json, fetch_text


class WttrAggregator(BaseWeatherAggregator):
    """Fallback weather aggregator using wttr.in service via centralized stealth client."""

    def get_weather(self, city: str, days: int = 3) -> str:
        """Fetch compact weather string from wttr.in."""
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

    def get_forecast(self, city: str, days: int = 3, hours: int = 12) -> str:
        """Fetch 3-day JSON forecast (with 3-hour intervals) from wttr.in (?format=j1), falling back to compact format."""
        try:
            encoded_city = urllib.parse.quote((city or "").strip())
            data = fetch_json(
                f"https://wttr.in/{encoded_city}?format={WeatherFormat.JSON}",
                headers=WEATHER_DEFAULT_HEADERS,
                timeout=5,
            )
            if isinstance(data, dict) and data.get("current_condition"):
                curr = data["current_condition"][0]
                desc_list = curr.get("weatherDesc") or [{}]
                desc = desc_list[0].get("value", "Unknown") if desc_list else "Unknown"
                temp_c = curr.get("temp_C", "N/A")
                temp_f = curr.get("temp_F", "N/A")
                feels_c = curr.get("FeelsLikeC", temp_c)
                humidity = curr.get("humidity", "N/A")
                wind_kmh = curr.get("windspeedKmph", "N/A")
                wind_dir = curr.get("winddir16Point", "")

                lines: List[str] = [
                    f"Current: {desc}, {temp_c}°C ({temp_f}°F) | Feels Like: {feels_c}°C | "
                    f"Humidity: {humidity}% | Wind: {wind_kmh} km/h {wind_dir}".strip()
                ]
                weather_days = data.get("weather") or []
                forecast_hours = max(0, min(int(hours if hours is not None else 12), 48))
                if weather_days and forecast_hours > 0:
                    hourly_slots = []
                    for day_obj in weather_days[:2]:
                        d_str = day_obj.get("date", "")
                        for h_entry in (day_obj.get("hourly") or []):
                            hourly_slots.append((d_str, h_entry))
                    max_slots = max(1, (forecast_hours + 2) // 3)
                    selected_slots = hourly_slots[:max_slots]
                    if selected_slots:
                        lines.append("\n=== HOURLY FORECAST (wttr.in 3-hr intervals) ===")
                        for d_str, h in selected_slots:
                            raw_t = str(h.get("time", "0")).zfill(4)
                            hh_mm = f"{raw_t[:2]}:{raw_t[2:]}"
                            h_desc_list = h.get("weatherDesc") or [{}]
                            h_desc = (h_desc_list[0].get("value", "") if h_desc_list else "") or desc
                            h_tc = h.get("tempC", "N/A")
                            h_tf = h.get("tempF", "N/A")
                            h_rain = h.get("chanceofrain", 0)
                            h_wind = h.get("windspeedKmph", "N/A")
                            lines.append(
                                f"- {d_str} {hh_mm}: {h_desc} | {h_tc}°C ({h_tf}°F) | "
                                f"Rain Chance: {h_rain}% | Wind: {h_wind} km/h"
                            )

                if weather_days:
                    lines.append(f"\n=== {min(len(weather_days), max(1, days))}-DAY FORECAST (wttr.in) ===")
                    for day_obj in weather_days[: max(1, days)]:
                        d_str = day_obj.get("date", "")
                        hi_c = day_obj.get("maxtempC", "N/A")
                        lo_c = day_obj.get("mintempC", "N/A")
                        hourly = day_obj.get("hourly") or []
                        mid_desc = ""
                        max_rain = 0
                        if hourly:
                            mid = hourly[len(hourly) // 2]
                            w_desc = mid.get("weatherDesc") or [{}]
                            mid_desc = w_desc[0].get("value", "") if w_desc else ""
                            max_rain = max(int(h.get("chanceofrain", 0) or 0) for h in hourly)
                        lines.append(
                            f"- {d_str}: {mid_desc or desc} | High: {hi_c}°C, Low: {lo_c}°C | Rain Chance: {max_rain}%"
                        )
                return "\n".join(lines)
        except Exception:
            pass
        return self.get_weather(city, days=days)

