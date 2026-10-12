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

def search_youtube_api(query: str, max_results: int = 1) -> list[dict]:
    """Searches YouTube using the official YouTube Data API v3."""
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        return []

    try:
        url = "https://www.googleapis.com/youtube/v3/search"
        params = {
            'part': 'snippet',
            'q': query,
            'type': 'video',
            'videoEmbeddable': 'true',
            'maxResults': max_results,
            'key': api_key
        }
        res = requests.get(url, params=params, timeout=10)
        if res.status_code != 200:
            return []
            
        data = res.json()
        results = []
        for item in data.get('items', []):
            vid = item['id'].get('videoId', '')
            if vid:
                results.append({
                    "id": vid,
                    "title": item['snippet'].get('title', 'Unknown Title'),
                    "channel": item['snippet'].get('channelTitle', 'Unknown Channel'),
                    "url": f"https://www.youtube.com/watch?v={vid}"
                })
        return results
    except Exception as e:
        print(f"⚠️ [YouTube API Error]: {e}")
        return []

def search_youtube_ddg(query: str, max_results: int = 1) -> list[dict]:
    """Fallback search using DuckDuckGo scraping to find working, embeddable YouTube videos."""
    queries_to_try = [
        f"site:youtube.com {query}",
        f"site:youtube.com {query} video"
    ]
    seen_ids = set()
    candidates = []

    for q in queries_to_try:
        try:
            res = list(DDGS().text(q, max_results=max_results + 4))
            for r in res:
                m = re.search(r'(?:v=|\/watch\?v=|\/embed\/|\/shorts\/|youtu\.be\/)([0-9A-Za-z_-]{11})(?:[?&/]|\b|$)', r.get('href', ''))
                if m:
                    vid = m.group(1)
                    if vid not in seen_ids:
                        seen_ids.add(vid)
                        candidates.append((vid, r.get('title', '')))
            if len(candidates) >= max_results + 3:
                break
        except Exception:
            continue

    verified_results = []
    for vid, raw_title in candidates:
        if not verify_embeddable(vid):
            continue

        oembed = get_video_oembed(vid)
        title = oembed["title"] or raw_title
        author = oembed["author"]

        verified_results.append({
            "id": vid,
            "title": title,
            "channel": author,
            "url": f"https://www.youtube.com/watch?v={vid}"
        })

        if len(verified_results) >= max_results:
            break

    return verified_results

def search_youtube(query: str, max_results: int = 1) -> list[dict]:
    """Main YouTube search client. Tries official API first, falls back to DuckDuckGo."""
    results = search_youtube_api(query, max_results=max_results)
    if not results:
        print(f"⚠️ [YouTube]: Falling back to DuckDuckGo search for '{query}'...")
        results = search_youtube_ddg(query, max_results=max_results)
    return results

