import os
import re
import requests
from ddgs import DDGS

def verify_embeddable(vid: str) -> bool:
    """Verifies that a YouTube video can be embedded on third-party sites like Discord."""
    try:
        embed_url = f"https://www.youtube.com/embed/{vid}"
        embed_text = requests.get(embed_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=3).text
        if "Playback on other websites has been disabled" in embed_text or "UNPLAYABLE" in embed_text:
            print(f"⚠️ [YouTube]: Skipping {vid} because embedding is disabled by owner.")
            return False
        return True
    except Exception:
        return True

def get_video_oembed(vid: str) -> dict:
    """Fetches video title and channel author via YouTube oEmbed."""
    try:
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json"
        res = requests.get(oembed_url, timeout=3)
        if res.status_code == 200:
            data = res.json()
            return {
                "title": data.get("title", ""),
                "author": data.get("author_name", "")
            }
    except Exception:
        pass
    return {"title": "", "author": ""}

from src.skills.youtube.aggregators import (
    YouTubeApiAggregator,
    DuckDuckGoYouTubeAggregator,
    YouTubeSearchManager,
)

_search_manager = YouTubeSearchManager(
    verify_embeddable_fn=verify_embeddable,
    get_oembed_fn=get_video_oembed
)

def search_youtube_api(query: str, max_results: int = 1) -> list[dict]:
    """Searches YouTube using the official YouTube Data API v3."""
    return _search_manager.api_aggregator.search(query, max_results=max_results)

def search_youtube_ddg(query: str, max_results: int = 1) -> list[dict]:
    """Fallback search using DuckDuckGo scraping to find working, embeddable YouTube videos."""
    return _search_manager.ddg_aggregator.search(query, max_results=max_results)

def search_youtube(query: str, max_results: int = 1) -> list[dict]:
    """Main YouTube search client. Tries official API first, falls back to DuckDuckGo."""
    return _search_manager.search(query, max_results=max_results)

