"""
Tools Registry Module
Assembles system tools, web tools, and domain skills into active toolsets
used by the agent modes (RESEARCH_TOOLS, CHAT_TOOLS).
"""

from src.tools.system import (
    get_current_datetime,
    evaluate_math,
    save_memory,
    recall_memories,
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
)
from src.skills import (
    skill_weather_reporter,
    skill_youtube_searcher,
    skill_top_music,
    skill_csgo_esports,
    skill_sports_info,
)

# Combined registries for mode execution
RESEARCH_TOOLS = {
    **SYSTEM_TOOLS,
    **WEB_TOOLS,
    "skill_weather_reporter": skill_weather_reporter,
    "skill_youtube_searcher": skill_youtube_searcher,
    "skill_top_music": skill_top_music,
    "skill_csgo_esports": skill_csgo_esports,
    "skill_sports_info": skill_sports_info,
}

CHAT_TOOLS = {
    **SYSTEM_TOOLS,
    **WEB_TOOLS,
    "skill_weather_reporter": skill_weather_reporter,
    "skill_youtube_searcher": skill_youtube_searcher,
    "skill_top_music": skill_top_music,
    "skill_csgo_esports": skill_csgo_esports,
    "skill_sports_info": skill_sports_info,
}

