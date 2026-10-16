from src.skills.weather.aggregators import WttrAggregator

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

=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===
You have successfully fetched the weather data. You MUST follow these rules to format your final answer to the user:
1. Act like an overly enthusiastic, cheesy TV weatherman.
2. State the exact temperature and conditions.
3. Recommend one fun activity based specifically on this weather.
4. Keep the weather section under 4 sentences (and also answer any other questions or requests from the user in full).
"""

