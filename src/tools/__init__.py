from src.tools.system import (
    get_current_datetime,
    evaluate_math,
    save_memory,
    recall_memories,
    SYSTEM_TOOLS,
)
from src.tools.web import (
    search_web,
    read_website,
    read_youtube_video,
    WEB_TOOLS,
    VISITED_URLS,
    URL_CACHE,
    SEARCH_CACHE,
    YOUTUBE_CACHE,
)
def __getattr__(name: str):
    if name in ("RESEARCH_TOOLS", "CHAT_TOOLS"):
        import src.registry as reg
        return getattr(reg, name)
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
    "get_current_datetime",
    "evaluate_math",
    "save_memory",
    "recall_memories",
    "search_web",
    "read_website",
    "read_youtube_video",
    "SYSTEM_TOOLS",
    "WEB_TOOLS",
    "RESEARCH_TOOLS",
    "CHAT_TOOLS",
    "VISITED_URLS",
    "URL_CACHE",
    "SEARCH_CACHE",
    "YOUTUBE_CACHE",
]

