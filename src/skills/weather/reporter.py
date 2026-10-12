import requests

def skill_weather_reporter(city: str) -> str:
    """
    A full 'Skill' packaged as a tool. Fetches the current weather for a city.
    Use this whenever the user asks about the weather.
    """
    print(f"🌤️ [Executing Weather Skill]: {city}")
    try:
        response = requests.get(f"https://wttr.in/{city}?format=%C+%t+%w+%h", timeout=5)
        weather_data = response.text.strip()
    except Exception as e:
        weather_data = f"Error fetching weather: {e}"
        
    return f"""
=== RAW WEATHER DATA ===
City: {city}
Conditions: {weather_data}

=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===
You have successfully fetched the weather data. You MUST follow these rules to format your final answer to the user:
1. Act like an overly enthusiastic, cheesy TV weatherman.
2. State the exact temperature and conditions.
3. Recommend one fun activity based specifically on this weather.
4. Keep your response under 4 sentences.
"""
