from src.skills.weather.aggregators import WttrAggregator
from src.skills.weather.templates import WEATHER_INSTRUCTIONS

_weather_aggregator = WttrAggregator()


def skill_weather_reporter(city: str) -> str:
    """
    A full 'Skill' packaged as a tool. Fetches the current weather for a city.
    Use this whenever the user asks about the weather.

    Args:
        city: City name, optionally with country (e.g. 'Paris, France').
    """
    print(f"🌤️ [Executing Weather Skill]: {city}")
    weather_data = _weather_aggregator.get_weather(city)

    return f"""
=== RAW WEATHER DATA ===
City: {city}
Conditions: {weather_data}

{WEATHER_INSTRUCTIONS}
"""

