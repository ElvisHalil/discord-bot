from typing import Optional

from src.skills.weather.aggregators import WeatherAggregatorManager
from src.skills.weather.templates import WEATHER_INSTRUCTIONS

_weather_aggregator = WeatherAggregatorManager()


def skill_weather_info(city: str, days: Optional[int] = 7) -> str:
    """
    A full 'Skill' packaged as a tool. Fetches current weather conditions and a multi-day forecast (up to 16 days) for a city.
    Use this whenever the user asks about current weather, tomorrow's weather, weekend weather, or a multi-day forecast.

    Args:
        city: City name, optionally with region or country (e.g. 'Paris, France' or 'Tokyo').
        days: Number of forecast days to include (1 to 16, default 7).
    """
    forecast_days = max(1, min(int(days or 7), 16))
    print(f"🌤️ [Executing Weather Skill]: {city} ({forecast_days}-day forecast)")
    weather_data = (
        _weather_aggregator.get_weather(city)
        if forecast_days == 7
        else _weather_aggregator.get_weather(city, days=forecast_days)
    )

    return f"""
=== RAW WEATHER DATA ===
City: {city}
Conditions: {weather_data}

{WEATHER_INSTRUCTIONS}
"""
