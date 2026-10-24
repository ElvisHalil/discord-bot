from typing import Optional
from src.skills.youtube.aggregators.base import BaseYouTubeAggregator
from src.skills.youtube.aggregators.api import YouTubeApiAggregator
from src.skills.youtube.aggregators.ddg import (
    DuckDuckGoYouTubeAggregator,
    is_trusted_or_requested_video_channel,
)

class YouTubeAggregatorManager:
    """
    Orchestrates YouTube search across multiple aggregators.
    Prioritizes free Direct YouTube + DuckDuckGo search (0 quota units) and gracefully falls back
    to official YouTube Data API v3 only when web search returns no valid results.
    """

    def __init__(self, verify_embeddable_fn=None, get_oembed_fn=None, is_short_fn=None, http_get_fn=None):
        self.verify_embeddable = verify_embeddable_fn
        self.get_video_oembed = get_oembed_fn
        self.is_short_fn = is_short_fn
        self.http_get_fn = http_get_fn
        self.api_aggregator = YouTubeApiAggregator()
        self.ddg_aggregator = DuckDuckGoYouTubeAggregator(
            verify_embeddable_fn=verify_embeddable_fn,
            get_oembed_fn=get_oembed_fn,
            is_short_fn=is_short_fn,
            http_get_fn=http_get_fn,
        )

    def search(self, query: str, max_results: int = 1) -> list[dict]:
        """Tries free Direct YouTube + DuckDuckGo search first (0 quota), falls back to official API if needed."""
        print(f"🔎 [YouTube Video Search]: Searching for '{query}'...")
        results = self.ddg_aggregator.search(query, max_results=max_results)
        if not results:
            print(f"⚠️ [YouTube Video Search]: Web search yielded no results, falling back to YouTube Data API for '{query}'...")
            api_results = self.api_aggregator.search(query, max_results=max_results + 4)
            official_embeddable = []
            official_blocked = []
            standard_embeddable = []
            standard_blocked = []
            for r in api_results:
                vid = r.get("id", "")
                title = r.get("title", "")
                chan = r.get("channel", "")
                if self.is_short_fn and self.is_short_fn(vid, title=title):
                    continue
                is_embed = True if not self.verify_embeddable else bool(self.verify_embeddable(vid))
                is_official = is_trusted_or_requested_video_channel(query, chan)
                r["embeddable"] = is_embed
                r["is_official_fallback"] = bool(not is_embed and is_official)
                if is_embed:
                    if is_official:
                        official_embeddable.append(r)
                    else:
                        standard_embeddable.append(r)
                else:
                    oembed = self.get_video_oembed(vid) if self.get_video_oembed else {"title": title}
                    if oembed.get("title"):
                        if is_official:
                            official_blocked.append(r)
                        else:
                            standard_blocked.append(r)
                if len(official_embeddable) >= max_results:
                    break
            combined_api = official_embeddable + official_blocked + standard_embeddable + standard_blocked
            results = combined_api[:max_results]
            if results:
                print(f"✅ [YouTube API]: Successfully found {len(results)} video(s) for '{query}'")
            else:
                print(f"❌ [YouTube API]: No videos found for '{query}'")
        else:
            print(f"✅ [YouTube Video Search]: Found {len(results)} verified video(s) for '{query}'")

        for r in results:
            chan_str = f" (Channel: {r.get('channel')})" if r.get('channel') else ""
            print(f"  └─ 🎬 [Video Resolver Result]: '{r.get('title')}'{chan_str} -> {r.get('url')}")
        return results

