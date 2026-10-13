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
)

# Combined registries for mode execution
RESEARCH_TOOLS = {
    **SYSTEM_TOOLS,
    **WEB_TOOLS,
    "skill_weather_reporter": skill_weather_reporter,
    "skill_youtube_searcher": skill_youtube_searcher,
    "skill_top_music": skill_top_music,
    "skill_audio_intelligence": skill_audio_intelligence,
    "skill_csgo_esports": skill_csgo_esports,
    "skill_sports_info": skill_sports_info,
}

# Chat mode is a casual persona on a small model: keep the tool list short so the
# schemas don't eat context and the model isn't tempted into niche tools.
CHAT_TOOLS = {
    "search_web": search_web,
    "read_website": read_website,
    "skill_youtube_searcher": skill_youtube_searcher,
    "skill_top_music": skill_top_music,
    "skill_weather_reporter": skill_weather_reporter,
    "save_memory": save_memory,
}

