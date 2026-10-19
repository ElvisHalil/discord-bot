"""
Tools & Skills Registry Module
Assembles system tools, web tools, and domain skills into active toolsets
used by the agent modes (RESEARCH_TOOLS, CHAT_TOOLS).
"""

from src.tools.system import (
    get_current_datetime,
    evaluate_math,
    save_memory,
    recall_memories,
    set_memory_file,
    SYSTEM_TOOLS,
    ACTIVE_MEMORY_FILE,
)
from src.tools.web import (
    search_web,
    read_website,
    read_youtube_video,
    WEB_TOOLS,
    BROWSER_HEADERS,
    VISITED_URLS,
    reset_turn_visited_urls,
    URL_CACHE,
    SEARCH_CACHE,
    YOUTUBE_CACHE,
)
from src.skills import (
    skill_weather_info,
    skill_youtube_search,
    skill_music_discovery,
    skill_music_analysis,
    skill_sports_esports,
    skill_sports_info,
    skill_sports_odds,
    skill_sports_tracker,
    skill_news_headlines,
    skill_flight_info,
    skill_flight_tracker,
    skill_markets_info,
    skill_voice_info,
)

# Combined registries for mode execution
RESEARCH_TOOLS = {
    **SYSTEM_TOOLS,
    **WEB_TOOLS,
    "skill_news_headlines": skill_news_headlines,
    "skill_weather_info": skill_weather_info,
    "skill_youtube_search": skill_youtube_search,
    "skill_music_discovery": skill_music_discovery,
    "skill_music_analysis": skill_music_analysis,
    "skill_sports_esports": skill_sports_esports,
    "skill_sports_info": skill_sports_info,
    "skill_sports_odds": skill_sports_odds,
    "skill_sports_tracker": skill_sports_tracker,
    "skill_flight_info": skill_flight_info,
    "skill_flight_tracker": skill_flight_tracker,
    "skill_markets_info": skill_markets_info,
    "skill_voice_info": skill_voice_info,
}

# Chat mode is a casual persona on a small model: keep the tool list short so the
# schemas don't eat context and the model isn't tempted into niche tools.
CHAT_TOOLS = {
    **SYSTEM_TOOLS,
    **WEB_TOOLS,
    "skill_youtube_search": skill_youtube_search,
    "skill_sports_tracker": skill_sports_tracker,
    "skill_flight_info": skill_flight_info,
    "skill_flight_tracker": skill_flight_tracker,
    "skill_markets_info": skill_markets_info,
    "skill_voice_info": skill_voice_info,
}

