import os
import re
import requests
from ddgs import DDGS

def verify_embeddable(vid: str) -> bool:
    """
    Verifies that a YouTube video can be embedded on third-party sites like Discord.
    Checks:
    1. Valid 11-char YouTube ID.
    2. Official YouTube oEmbed API returns 200 OK (returns 401 if owner disabled embedding or video is age-restricted).
    3. Embed page (https://www.youtube.com/embed/{vid}) does not contain unplayable or disabled markers.
    """
    if not vid or not re.match(r'^[0-9A-Za-z_-]{11}$', vid):
        return False
    try:
        # Step 1: oEmbed check (standard third-party embed gateway used by Discord)
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json"
        oembed_res = requests.get(oembed_url, timeout=3)
        if oembed_res.status_code != 200:
            print(f"⚠️ [YouTube]: Skipping {vid} because oEmbed status is {oembed_res.status_code} (not embeddable).")
            return False

        # Step 2: Embed page response check (simulating Discord's third-party iframe Referer & origin
        # so label/owner restrictions like UMG/SME "blocked it from display on this website" are triggered)
        embed_url = f"https://www.youtube.com/embed/{vid}?origin=https://discord.com&widget_referrer=https://discord.com"
        embed_res = requests.get(
            embed_url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://discord.com/',
            },
            timeout=3
        )
        if embed_res.status_code != 200:
            return False

        embed_text = embed_res.text
        unplayable_markers = (
            "Playback on other websites has been disabled",
            "blocked it from display on this website or application",
            '"playableInEmbed":false',
            '"playableInEmbed": false',
            "UNPLAYABLE",
            "LOGIN_REQUIRED",
            "Video unavailable",
            "This video is unavailable",
            "This video is private",
            "error-message",
        )
        if any(marker in embed_text for marker in unplayable_markers):
            print(f"⚠️ [YouTube]: Skipping {vid} because embed page indicated unplayable/disabled on Discord.")
            return False

        return True
    except Exception as e:
        print(f"⚠️ [YouTube]: Verification failed for {vid}: {e}")
        return False

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

def is_youtube_short(vid: str, href: str = "", title: str = "") -> bool:
    """Detects if a YouTube video is a Short via URL structure, title tags, or HTTP redirect check."""
    if "/shorts/" in (href or "").lower():
        return True
    if "#shorts" in (title or "").lower() or "#short" in (title or "").lower():
        return True
    try:
        r = requests.head(f"https://www.youtube.com/shorts/{vid}", allow_redirects=False, timeout=3)
        # Shorts return HTTP 200; standard videos redirect (HTTP 303/302) to /watch?v=
        if r.status_code == 200:
            return True
    except Exception:
        pass
    return False

from src.skills.youtube.aggregators import (
    YouTubeApiAggregator,
    DuckDuckGoYouTubeAggregator,
    YouTubeSearchManager,
)

_search_manager = YouTubeSearchManager(
    verify_embeddable_fn=verify_embeddable,
    get_oembed_fn=get_video_oembed,
    is_short_fn=is_youtube_short
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

