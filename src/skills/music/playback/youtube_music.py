import os
import re
import requests
from ddgs import DDGS
from src.music_utils import parse_song_meta, MUSIC_METADATA_CACHE
from src.skills.youtube.client import verify_embeddable, get_video_oembed

def resolve_youtube_music_link(query: str, max_results: int = 1) -> list[dict]:
    """
    Finds verified YouTube Music links for a song query.
    Prioritizes official '[Artist] - Topic' channels so tracks play in true Song Mode with album art.
    """
    print(f"🎵 [YouTube Music Resolver]: Searching for '{query}'...")
    api_key = os.getenv("YOUTUBE_API_KEY")
    candidates = []
    seen_ids = set()

    # 1. Try YouTube Data API if configured
    if api_key:
        try:
            url = "https://www.googleapis.com/youtube/v3/search"
            params = {
                'part': 'snippet',
                'q': query,
                'type': 'video',
                'videoEmbeddable': 'true',
                'videoCategoryId': '10',  # Music category
                'maxResults': max_results + 3,
                'key': api_key
            }
            res = requests.get(url, params=params, timeout=10)
            if res.status_code == 200:
                for item in res.json().get('items', []):
                    vid = item['id'].get('videoId', '')
                    if vid and vid not in seen_ids:
                        seen_ids.add(vid)
                        candidates.append((vid, item['snippet'].get('title', ''), item['snippet'].get('channelTitle', '')))
        except Exception as e:
            print(f"⚠️ [YouTube Music API]: {e}")

    # 2. Fall back to DuckDuckGo if needed
    if not candidates:
        queries = [
            f'site:youtube.com {query} Topic',
            f'site:music.youtube.com {query}',
            f'site:youtube.com {query}'
        ]
        if " by " in query:
            queries.append(f"site:youtube.com {query.replace(' by ', ' ')}")

        for q in queries:
            try:
                for r in list(DDGS().text(q, max_results=max_results + 4)):
                    m = re.search(r'(?:v=|\/watch\?v=|\/embed\/|\/shorts\/|youtu\.be\/)([0-9A-Za-z_-]{11})(?:[?&/]|\b|$)', r.get('href', ''))
                    if m:
                        vid = m.group(1)
                        if vid not in seen_ids:
                            seen_ids.add(vid)
                            candidates.append((vid, r.get('title', ''), ''))
                if len(candidates) >= max_results + 3:
                    break
            except Exception:
                continue

    topic_results = []
    standard_results = []

    for vid, raw_title, raw_author in candidates:
        if not verify_embeddable(vid):
            continue

        oembed = get_video_oembed(vid)
        title = oembed["title"] or raw_title
        author = oembed["author"] or raw_author

        is_topic = " - Topic" in author or "Topic" in author
        link = f"https://music.youtube.com/watch?v={vid}"

        meta = parse_song_meta(title, author, query=query)
        MUSIC_METADATA_CACHE[vid] = {
            "song": meta["song"],
            "artist": meta["artist"],
            "genre": meta.get("genre"),
            "album": meta.get("album"),
            "url": link
        }

        entry = {
            "id": vid,
            "title": meta["song"],
            "artist": meta["artist"],
            "genre": meta.get("genre"),
            "album": meta.get("album"),
            "url": link,
            "is_topic": is_topic
        }

        if is_topic:
            topic_results.append(entry)
            if len(topic_results) >= max_results:
                break
        else:
            standard_results.append(entry)

    final_results = topic_results if topic_results else standard_results
    if not final_results and candidates:
        vid, raw_title, _ = candidates[0]
        link = f"https://music.youtube.com/watch?v={vid}"
        meta = parse_song_meta(raw_title, "", query=query)
        final_results = [{
            "id": vid,
            "title": meta["song"],
            "artist": meta["artist"],
            "genre": meta.get("genre"),
            "album": meta.get("album"),
            "url": link,
            "is_topic": False
        }]

    return final_results[:max_results]

