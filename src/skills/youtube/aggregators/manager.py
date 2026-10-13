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
        self.verify_embeddable = verify_embeddable_fn
        self.get_video_oembed = get_oembed_fn
        self.is_short_fn = is_short_fn
        self.api_aggregator = YouTubeApiAggregator()
        self.ddg_aggregator = DuckDuckGoYouTubeAggregator(
            verify_embeddable_fn=verify_embeddable_fn,
            get_oembed_fn=get_oembed_fn,
            is_short_fn=is_short_fn
        )

    def search(self, query: str, max_results: int = 1) -> list[dict]:
        """Tries free DuckDuckGo search first (0 quota), falls back to official API if needed."""
        print(f"🔎 [YouTube Video Search]: Searching via DuckDuckGo for '{query}'...")
        results = self.ddg_aggregator.search(query, max_results=max_results)
        if not results:
            print(f"⚠️ [YouTube Video Search]: DuckDuckGo yielded no embeddable results, falling back to YouTube Data API for '{query}'...")
            api_results = self.api_aggregator.search(query, max_results=max_results + 3)
            verified_api_results = []
            for r in api_results:
                vid = r.get("id", "")
                if self.is_short_fn and self.is_short_fn(vid, title=r.get("title", "")):
                    continue
                if self.verify_embeddable and not self.verify_embeddable(vid):
                    continue
                verified_api_results.append(r)
                if len(verified_api_results) >= max_results:
                    break
            results = verified_api_results
            if results:
                print(f"✅ [YouTube API]: Successfully found {len(results)} video(s) for '{query}'")
            else:
                print(f"❌ [YouTube API]: No videos found for '{query}'")
        else:
            print(f"✅ [YouTube DDG]: Found {len(results)} verified video(s) for '{query}'")

        for r in results:
            chan_str = f" (Channel: {r.get('channel')})" if r.get('channel') else ""
            print(f"  └─ 🎬 [Video Resolver Result]: '{r.get('title')}'{chan_str} -> {r.get('url')}")
        return results

