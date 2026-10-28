"""
Tools & Skills Registry Module
Assembles system tools, web tools, and domain skills into active toolsets
used by the agent modes (RESEARCH_TOOLS, CHAT_TOOLS).

Supports optional skill toggling via the `DISABLED_SKILLS` environment variable
(e.g., `DISABLED_SKILLS=flights,sports,music`).
"""

import os
import re
from typing import Any, Callable, Dict, Optional, Set

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
    search_images,
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
    skill_graphing_info,
    skill_voice_info,
    skill_job_manager,
)

# Maps domain skill names (and singular aliases) to their registered tool entrypoints
SKILL_DOMAIN_TO_TOOLS: Dict[str, tuple[str, ...]] = {
    "news": ("skill_news_headlines",),
    "weather": ("skill_weather_info",),
    "youtube": ("skill_youtube_search",),
    "music": ("skill_music_discovery", "skill_music_analysis"),
    "sports": (
        "skill_sports_esports",
        "skill_sports_info",
        "skill_sports_odds",
        "skill_sports_tracker",
    ),
    "sport": (
        "skill_sports_esports",
        "skill_sports_info",
        "skill_sports_odds",
        "skill_sports_tracker",
    ),
    "flights": ("skill_flight_info", "skill_flight_tracker"),
    "flight": ("skill_flight_info", "skill_flight_tracker"),
    "markets": ("skill_markets_info",),
    "market": ("skill_markets_info",),
    "graphing": ("skill_graphing_info",),
    "voice": ("skill_voice_info",),
    "jobs": ("skill_job_manager",),
    "job": ("skill_job_manager",),
    "images": ("search_images",),
    "image": ("search_images",),
}


def get_disabled_tool_names(disabled_csv: Optional[str] = None) -> Set[str]:
    """
    Resolve `DISABLED_SKILLS` (comma/semicolon/space-separated skill folder names
    like `flights,sports,music` or explicit tool names like `skill_flight_info`)
    into a set of disabled tool function names.
    """
    raw = os.getenv("DISABLED_SKILLS", "") if disabled_csv is None else disabled_csv
    if not raw or not raw.strip():
        return set()
    disabled_tools: Set[str] = set()
    for token in re.split(r"[,;\s]+", raw.strip().lower()):
        if not token:
            continue
        if token in SKILL_DOMAIN_TO_TOOLS:
            disabled_tools.update(SKILL_DOMAIN_TO_TOOLS[token])
        elif token.startswith("skill_") or token in ("search_images", "search_web", "read_website", "read_youtube_video"):
            disabled_tools.add(token)
    return disabled_tools


def filter_toolset(
    toolset: Dict[str, Callable[..., Any]],
    disabled_csv: Optional[str] = None,
) -> Dict[str, Callable[..., Any]]:
    """Return a copy of `toolset` with any tools matched by `DISABLED_SKILLS` removed."""
    disabled = get_disabled_tool_names(disabled_csv)
    if not disabled:
        return dict(toolset)
    return {name: fn for name, fn in toolset.items() if name not in disabled}


_ALL_RESEARCH_TOOLS: Dict[str, Callable[..., Any]] = {
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
    "skill_graphing_info": skill_graphing_info,
    "skill_voice_info": skill_voice_info,
    "skill_job_manager": skill_job_manager,
    "search_images": search_images,
}

_ALL_CHAT_TOOLS: Dict[str, Callable[..., Any]] = {
    **SYSTEM_TOOLS,
    "skill_youtube_search": skill_youtube_search,
    "skill_voice_info": skill_voice_info,
    "skill_job_manager": skill_job_manager,
}

# Combined registries for mode execution
RESEARCH_TOOLS = filter_toolset(_ALL_RESEARCH_TOOLS)

# Chat mode is a casual persona on a small model: keep the tool list short so the
# schemas don't eat context and the model isn't tempted into niche tools.
CHAT_TOOLS = filter_toolset(_ALL_CHAT_TOOLS)
