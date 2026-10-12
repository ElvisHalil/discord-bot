from typing import Optional
from src.skills.youtube.aggregators.base import BaseYouTubeSearchAggregator
from src.skills.youtube.aggregators.api import YouTubeApiAggregator
from src.skills.youtube.aggregators.ddg import DuckDuckGoYouTubeAggregator

class YouTubeSearchManager:
    """
    Orchestrates YouTube search across multiple aggregators.
    Prioritizes free DuckDuckGo search (0 quota units) and gracefully falls back
    to official YouTube Data API v3 only when web search returns no valid results.
    """

    def __init__(self, verify_embeddable_fn=None, get_oembed_fn=None, is_short_fn=None):
        self.api_aggregator = YouTubeApiAggregator()
        self.ddg_aggregator = DuckDuckGoYouTubeAggregator(
            verify_embeddable_fn=verify_embeddable_fn,
            get_oembed_fn=get_oembed_fn,
            is_short_fn=is_short_fn
        )

    def search(self, query: str, max_results: int = 1) -> list[dict]:
        """Tries free DuckDuckGo search first (0 quota), falls back to official API if needed."""
        print(f"🔎 [YouTube]: Searching via DuckDuckGo for '{query}'...")
        results = self.ddg_aggregator.search(query, max_results=max_results)
        if not results:
            print(f"⚠️ [YouTube]: DuckDuckGo yielded no embeddable results, falling back to YouTube Data API for '{query}'...")
            results = self.api_aggregator.search(query, max_results=max_results)
        return results

