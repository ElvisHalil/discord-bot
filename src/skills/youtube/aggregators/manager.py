from typing import Optional
from src.skills.youtube.aggregators.base import BaseYouTubeSearchAggregator
from src.skills.youtube.aggregators.api import YouTubeApiAggregator
from src.skills.youtube.aggregators.ddg import DuckDuckGoYouTubeAggregator

class YouTubeSearchManager:
    """
    Orchestrates YouTube search across multiple aggregators.
    Tries the official YouTube API v3 first, and gracefully falls back to DuckDuckGo search.
    """

    def __init__(self, verify_embeddable_fn=None, get_oembed_fn=None):
        self.api_aggregator = YouTubeApiAggregator()
        self.ddg_aggregator = DuckDuckGoYouTubeAggregator(
            verify_embeddable_fn=verify_embeddable_fn,
            get_oembed_fn=get_oembed_fn
        )

    def search(self, query: str, max_results: int = 1) -> list[dict]:
        """Tries official API first, falls back to DuckDuckGo scraping."""
        results = self.api_aggregator.search(query, max_results=max_results)
        if not results:
            print(f"⚠️ [YouTube]: Falling back to DuckDuckGo search for '{query}'...")
            results = self.ddg_aggregator.search(query, max_results=max_results)
        return results
