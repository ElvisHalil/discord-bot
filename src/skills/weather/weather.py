from typing import Optional

from src.skills.weather.aggregators import WeatherAggregatorManager
from src.skills.weather.templates import WEATHER_INSTRUCTIONS

_weather_aggregator = WeatherAggregatorManager()


def skill_weather_info(city: str, days: Optional[int] = 7, hours: Optional[int] = 12) -> str:
    """
    A full 'Skill' packaged as a tool. Fetches current weather conditions, an hour-by-hour forecast (up to 48 hours),
    and a multi-day forecast (up to 16 days) for a city.
    Use this whenever the user asks about current weather, hourly forecast, tonight's weather, tomorrow's weather,
    weekend weather, or a multi-day forecast.

    Args:
        city: City name, optionally with region or country (e.g. 'Paris, France' or 'Tokyo').
        days: Number of forecast days to include (1 to 16, default 7).
        hours: Number of upcoming hourly forecast slots to include (0 to 48, default 12).
    """
    forecast_days = max(1, min(int(days or 7), 16))
    forecast_hours = max(0, min(int(hours if hours is not None else 12), 48))
    print(f"🌤️ [Executing Weather Skill]: {city} ({forecast_days}-day, {forecast_hours}-hour forecast)")
    if forecast_days == 7 and forecast_hours == 12:
        weather_data = _weather_aggregator.get_weather(city)
    elif forecast_hours == 12:
        weather_data = _weather_aggregator.get_weather(city, days=forecast_days)
    else:
        weather_data = _weather_aggregator.get_weather(city, days=forecast_days, hours=forecast_hours)

    return f"""
=== RAW WEATHER DATA ===
City: {city}
Conditions: {weather_data}

{WEATHER_INSTRUCTIONS}
"""

