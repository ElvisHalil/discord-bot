import logging
import re
from src.skills.youtube.aggregators import (
    YouTubeAggregatorManager,
)
from src.skills.youtube.catalogs import UNPLAYABLE_EMBED_MARKERS
from src.utils.cache import BoundedCache
from src.utils.http import ThreadLocalRequestsSession
from src.utils.workers import submit_to_worker

logger = logging.getLogger(__name__)

# Thread-local persistent HTTP session for connection pooling (reuses TCP/TLS handshakes safely across worker threads)
_HTTP_SESSION = ThreadLocalRequestsSession(pool_connections=10, pool_maxsize=20)


def _http_get(url: str, **kwargs):
    return _HTTP_SESSION.get(url, **kwargs)


def _http_head(url: str, **kwargs):
    return _HTTP_SESSION.head(url, **kwargs)


youtube_http_get = _http_get


# Bounded in-memory caches to eliminate duplicate network requests across resolution & post-processing
_OEMBED_CACHE: dict[str, dict] = BoundedCache(maxsize=512)
_EMBEDDABLE_CACHE: dict[str, bool] = BoundedCache(maxsize=512)
_SHORT_CACHE: dict[str, bool] = BoundedCache(maxsize=512)


def get_cached_oembed(vid: str) -> dict | None:
    """Return cached oEmbed metadata for a video ID if already verified in memory."""
    return _OEMBED_CACHE.get(vid) if vid else None


def clear_youtube_caches() -> None:
    """Clears in-memory YouTube verification and oEmbed caches."""
    _OEMBED_CACHE.clear()
    _EMBEDDABLE_CACHE.clear()
    _SHORT_CACHE.clear()


def verify_embeddable(vid: str) -> bool:
    """
    Verifies that a YouTube video can be embedded on third-party sites like Discord.
    Checks:
    1. Valid 11-char YouTube ID.
    2. Official YouTube oEmbed API returns 200 OK (and caches title/author for 0ms reuse).
    3. Embed page (https://www.youtube.com/embed/{vid}) with Discord's third-party Referer/origin
       does not contain unplayable or label-disabled markers.
    """
    if not vid or not re.match(r'^[0-9A-Za-z_-]{11}$', vid):
        return False
    if vid in _EMBEDDABLE_CACHE:
        return _EMBEDDABLE_CACHE[vid]
    try:
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json"
        embed_url = f"https://www.youtube.com/embed/{vid}?origin=https://discord.com&widget_referrer=https://discord.com"
        embed_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://discord.com/',
        }

        need_oembed = vid not in _OEMBED_CACHE

        if need_oembed:
            # Fire oEmbed and embed-page requests concurrently over persistent keep-alive pool
            fut_oembed = submit_to_worker(_http_get, oembed_url, timeout=3)
            fut_embed = submit_to_worker(_http_get, embed_url, headers=embed_headers, timeout=3)
            oembed_res = fut_oembed.result()
            embed_res = fut_embed.result()
            if oembed_res.status_code != 200:
                print(f"⚠️ [YouTube]: Skipping {vid} because oEmbed status is {oembed_res.status_code} (not embeddable).")
                _EMBEDDABLE_CACHE[vid] = False
                return False
            try:
                data = oembed_res.json()
                if isinstance(data, dict) and data.get("title"):
                    author_name = data.get("author_name", "")
                    _OEMBED_CACHE[vid] = {
                        "title": data.get("title", ""),
                        "author": author_name
                    }
                    # Auto-generated YouTube Music '- Topic' art tracks are never YouTube Shorts
                    if " - Topic" in author_name:
                        _SHORT_CACHE[vid] = False
            except (ValueError, TypeError, KeyError) as e:
                logger.debug(f"YouTube oEmbed JSON parse skipped for {vid}: {e}")
        else:
            embed_res = _http_get(embed_url, headers=embed_headers, timeout=3)

        if embed_res.status_code != 200:
            _EMBEDDABLE_CACHE[vid] = False
            return False

        embed_text = embed_res.text
        if any(marker in embed_text for marker in UNPLAYABLE_EMBED_MARKERS):
            print(f"⚠️ [YouTube]: Skipping {vid} because embed page indicated unplayable/disabled on Discord.")
            _EMBEDDABLE_CACHE[vid] = False
            return False

        _EMBEDDABLE_CACHE[vid] = True
        return True
    except Exception as e:
        print(f"⚠️ [YouTube]: Verification failed for {vid}: {e}")
        return False


def get_video_oembed(vid: str) -> dict:
    """Fetches video title and channel author via YouTube oEmbed (using in-memory cache when available)."""
    if vid and vid in _OEMBED_CACHE:
        return _OEMBED_CACHE[vid]
    try:
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json"
        res = _http_get(oembed_url, timeout=3)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, dict):
                author_name = data.get("author_name", "")
                entry = {
                    "title": data.get("title", ""),
                    "author": author_name
                }
                if vid and entry["title"]:
                    _OEMBED_CACHE[vid] = entry
                    if " - Topic" in author_name:
                        _SHORT_CACHE[vid] = False
                return entry
    except Exception as e:
        logger.debug(f"get_video_oembed failed for {vid}: {e}")
    return {"title": "", "author": ""}


def is_youtube_short(vid: str, href: str = "", title: str = "") -> bool:
    """Detects if a YouTube video is a Short via URL structure, title tags, or HTTP redirect check."""
    href_low = (href or "").lower()
    title_low = (title or "").lower()
    if "/shorts/" in href_low:
        if vid:
            _SHORT_CACHE[vid] = True
        return True
    if "#shorts" in title_low or "#short" in title_low:
        if vid:
            _SHORT_CACHE[vid] = True
        return True
    if vid and vid in _SHORT_CACHE:
        return _SHORT_CACHE[vid]
    if "music.youtube.com" in href_low:
        if vid:
            _SHORT_CACHE[vid] = False
        return False
    # If oEmbed already identified this video as an official '- Topic' art track, it is never a Short
    if vid and " - Topic" in _OEMBED_CACHE.get(vid, {}).get("author", ""):
        _SHORT_CACHE[vid] = False
        return False
    try:
        r = _http_head(f"https://www.youtube.com/shorts/{vid}", allow_redirects=False, timeout=2.5)
        # Shorts return HTTP 200; standard videos redirect (HTTP 303/302) to /watch?v=
        is_short = (r.status_code == 200)
        if vid:
            _SHORT_CACHE[vid] = is_short
        return is_short
    except Exception as e:
        logger.debug(f"is_youtube_short HEAD check failed for {vid}: {e}")
    return False


_search_manager = YouTubeAggregatorManager(
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

