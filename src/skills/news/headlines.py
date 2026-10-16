"""
Top headlines skill from Google News' free RSS feeds (no API key).

DuckDuckGo news is keyword-matched, so "top headlines in Canada" returns articles
that merely contain those words. Google News RSS returns the actual ranked top
stories for a country edition or topic section.

Feed article links are encoded news.google.com URLs that only redirect via
JavaScript, so read_website can't follow them. decode_google_news_url() resolves
them to the publisher URL through the same endpoint the Google News web page uses.
That endpoint is undocumented: if it fails we keep the Google link (it still opens
fine in a browser).
"""

import json
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import quote, quote_plus, urlparse

import requests
from bs4 import BeautifulSoup

_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

# Countries with an English Google News edition: region alias -> (hl, gl)
_EDITIONS = {
    "us": ("en-US", "US"), "usa": ("en-US", "US"), "united states": ("en-US", "US"), "america": ("en-US", "US"),
    "canada": ("en-CA", "CA"), "ca": ("en-CA", "CA"),
    "uk": ("en-GB", "GB"), "united kingdom": ("en-GB", "GB"), "britain": ("en-GB", "GB"),
    "great britain": ("en-GB", "GB"), "england": ("en-GB", "GB"),
    "australia": ("en-AU", "AU"), "india": ("en-IN", "IN"), "ireland": ("en-IE", "IE"),
    "new zealand": ("en-NZ", "NZ"), "south africa": ("en-ZA", "ZA"), "singapore": ("en-SG", "SG"),
}
_GLOBAL_REGIONS = {"world", "global", "international", "worldwide"}

# Google News section topics
_TOPICS = {
    "world": "WORLD", "international": "WORLD", "nation": "NATION", "national": "NATION",
    "business": "BUSINESS", "economy": "BUSINESS", "finance": "BUSINESS",
    "technology": "TECHNOLOGY", "tech": "TECHNOLOGY",
    "entertainment": "ENTERTAINMENT", "sports": "SPORTS", "sport": "SPORTS",
    "science": "SCIENCE", "health": "HEALTH",
}
# Topic values that just mean "the main headlines" when a region is given.
_GENERIC_TOPICS = {
    "world", "international", "global", "news", "general", "top", "top stories", "top news",
    "headlines", "top headlines", "latest", "breaking", "all", "nation", "national", "politics",
}

MAX_HEADLINES = 8
CACHE_TTL_SECONDS = 600
_CACHE = {}


def decode_google_news_url(url: str, timeout: float = 8.0) -> Optional[str]:
    """Resolve a news.google.com/rss/articles/... link to the publisher's URL, or None."""
    try:
        parsed = urlparse(url)
        if parsed.hostname != "news.google.com" or "/articles/" not in parsed.path:
            return None
        article_id = parsed.path.rstrip("/").split("/")[-1]
        page = requests.get(f"https://news.google.com/rss/articles/{article_id}", headers=_UA, timeout=timeout)
        div = BeautifulSoup(page.text, "html.parser").select_one("c-wiz > div[jscontroller]")
        if div is None or not div.get("data-n-a-sg") or not div.get("data-n-a-ts"):
            return None
        inner = [
            "garturlreq",
            [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
             "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
            article_id, int(div["data-n-a-ts"]), div["data-n-a-sg"],
        ]
        payload = [[["Fbv4je", json.dumps(inner), None, "generic"]]]
        resp = requests.post(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute",
            headers={**_UA, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            data=f"f.req={quote(json.dumps(payload))}",
            timeout=timeout,
        )
        if resp.status_code != 200:
            return None
        data = json.loads(resp.text.split("\n\n")[1])[:-2]
        decoded = json.loads(data[0][2])[1]
        return decoded if isinstance(decoded, str) and decoded.startswith("http") else None
    except Exception:
        return None


def _build_feed_url(region: str, topic: str):
    """Return (feed_url, description) for a region/topic combination."""
    region_key = region.lower().strip()
    topic_key = topic.lower().strip()
    edition = _EDITIONS.get(region_key)
    is_global = not region_key or region_key in _GLOBAL_REGIONS
    # Small models often pass topic='world'/'news' when they just mean "top stories".
    if topic_key in _GENERIC_TOPICS and not is_global:
        topic, topic_key = "", ""

    def params(hl, gl):
        return f"hl={hl}&gl={gl}&ceid={gl}:{hl.split('-')[0]}"

    if edition or is_global:
        hl, gl = edition or ("en-US", "US")
        section = _TOPICS.get(topic_key) or ("WORLD" if is_global and not topic_key else None)
        if section:
            label = f"{section.title()} - {region or 'World'}"
            return f"https://news.google.com/rss/headlines/section/topic/{section}?{params(hl, gl)}", label
        if not topic_key:
            return f"https://news.google.com/rss?{params(hl, gl)}", f"Top stories - {region}"
        # Unknown topic within a country edition: search inside that edition
        q = quote_plus(f"{topic} when:1d")
        return f"https://news.google.com/rss/search?q={q}&{params(hl, gl)}", f"{topic} - {region or 'World'}"

    # Region without its own English edition (e.g. Europe, Germany): recent stories about it
    q = quote_plus(f"{(topic + ' ') if topic else ''}{region} when:1d")
    return f"https://news.google.com/rss/search?q={q}&{params('en-US', 'US')}", f"{(topic + ' - ') if topic else ''}{region}"


def _format_date(pub: str) -> str:
    try:
        return parsedate_to_datetime(pub).strftime("%b %d, %Y %H:%M UTC")
    except Exception:
        return pub or ""


def skill_top_headlines(region: Optional[str] = None, topic: Optional[str] = None) -> str:
    """Get today's actual top news headlines (ranked by Google News) for a country, region or topic.
    Use this for "top/latest headlines" or "what's in the news" requests. For news about one
    specific subject or event, use `search_web` with news=true instead.

    Args:
        region: Country or region, e.g. 'Canada', 'USA', 'UK', 'Europe', 'Germany'. Leave empty for world news.
        topic: Leave EMPTY for a region's main headlines. Only set it for a specific section: business, technology, entertainment, sports, science or health.
    """
    region = (region or "").strip()
    topic = (topic or "").strip()
    feed_url, label = _build_feed_url(region, topic)

    cached = _CACHE.get(feed_url)
    if cached and time.time() - cached[0] < CACHE_TTL_SECONDS:
        print(f"⚡ [Headlines Cache Hit]: {label}")
        return cached[1]

    print(f"📰 [Top Headlines Skill]: {label}")
    try:
        resp = requests.get(feed_url, headers=_UA, timeout=10)
        resp.raise_for_status()
        items = ET.fromstring(resp.content).findall("./channel/item")[:MAX_HEADLINES]
    except Exception as e:
        return f"❌ Could not load headlines for '{label}' ({e}). Use `search_web` with news=true instead."

    if not items:
        return f"No headlines found for '{label}'. Use `search_web` with news=true instead."

    stories = []
    for it in items:
        source_el = it.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""
        title = (it.findtext("title") or "").strip()
        if source and title.endswith(f" - {source}"):
            title = title[: -len(f" - {source}")].strip()
        stories.append({
            "title": title,
            "source": source,
            "date": _format_date(it.findtext("pubDate") or ""),
            "link": (it.findtext("link") or "").strip(),
        })

    # Resolve publisher URLs in parallel via shared worker pool so read_website can open them later.
    from src.utils.workers import map_in_workers
    decoded = map_in_workers(lambda s: decode_google_news_url(s["link"]), stories)

    output = f"=== TOP HEADLINES: {label} (Google News) ===\n"
    for i, (story, real_url) in enumerate(zip(stories, decoded), 1):
        meta = " | ".join(p for p in (story["source"], story["date"]) if p)
        output += f"{i}. {story['title']} [{meta}]\nURL: {real_url or story['link']}\n\n"

    output += (
        "--- RESPONSE FORMATTING INSTRUCTIONS FOR YOU (THE LLM) ---\n"
        "• Bold heading for this region/topic, then one bullet per story: - **Headline** — one-sentence summary. [Source Name](<URL>)\n"
        "• Only headlines are listed above, not article text: keep summaries to what the headline says. "
        "To expand on a story, call `read_website` on its URL first.\n"
        "• Copy each URL exactly and keep it inside <> so Discord shows no preview card. No second list or raw link dump."
    )

    _CACHE[feed_url] = (time.time(), output)
    return output


# Backward compatibility alias
get_top_headlines = skill_top_headlines
