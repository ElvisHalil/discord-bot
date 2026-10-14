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
    URL_CACHE,
    SEARCH_CACHE,
    YOUTUBE_CACHE,
)
from src.skills import (
    skill_weather_reporter,
    skill_youtube_searcher,
    skill_top_music,
    skill_audio_intelligence,
    skill_csgo_esports,
    skill_sports_info,
    skill_betting_odds,
    skill_track_live_game,
    skill_top_headlines,
)

# Combined registries for mode execution
RESEARCH_TOOLS = {
    **SYSTEM_TOOLS,
    **WEB_TOOLS,
    "skill_top_headlines": skill_top_headlines,
    "get_top_headlines": skill_top_headlines,  # Alias for backward compatibility
    "skill_weather_reporter": skill_weather_reporter,
    "skill_youtube_searcher": skill_youtube_searcher,
    "skill_top_music": skill_top_music,
    "skill_audio_intelligence": skill_audio_intelligence,
    "skill_csgo_esports": skill_csgo_esports,
    "skill_sports_info": skill_sports_info,
    "skill_betting_odds": skill_betting_odds,
    "skill_track_live_game": skill_track_live_game,
}

# Chat mode is a casual persona on a small model: keep the tool list short so the
# schemas don't eat context and the model isn't tempted into niche tools.
CHAT_TOOLS = {
    **SYSTEM_TOOLS,
    **WEB_TOOLS,
    "skill_youtube_searcher": skill_youtube_searcher,
    "skill_track_live_game": skill_track_live_game,
}

