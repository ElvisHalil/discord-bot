import os
import re
import requests
from dotenv import load_dotenv
from ddgs import DDGS
from src.music_utils import parse_song_meta, MUSIC_METADATA_CACHE
from src.skills.youtube.client import verify_embeddable, get_video_oembed, is_youtube_short

load_dotenv()

GARBAGE_MUSIC_KEYWORDS = [
    "tutorial", "how to play", "how to make", "reaction", "reacting",
    "reacts", "review", "podcast", "interview", "behind the scenes",
    "gameplay", "walkthrough", "tier list", "parody", "lesson",
    "drum cover", "synth tutorial", "unboxing", "vlog", "prank",
    "meme", "tiktok compilation", "nightcore", "sped up", "slowed",
    "vocal coach", "reverb"
]

def is_garbage_music_title(title: str, query: str) -> bool:
    """Detects if candidate title contains tutorial, reaction, or non-song keywords."""
    title_low = title.lower()
    query_low = query.lower()
    for kw in GARBAGE_MUSIC_KEYWORDS:
        if kw in title_low and kw not in query_low:
            return True
    return False

def extract_music_tokens(text: str) -> set[str]:
    """Tokenizes alphanumeric words excluding common musical and grammatical stop words."""
    words = re.findall(r'[a-zA-Z0-9]+', text.lower())
    stopwords = {
        "by", "the", "a", "an", "and", "or", "of", "in", "on", "at", "to",
        "for", "with", "feat", "ft", "official", "video", "audio", "music",
        "topic", "remix", "original", "mix", "full", "hd", "4k", "hq"
    }
    return {w for w in words if w not in stopwords and len(w) > 1}

def score_music_candidate(query: str, title: str, author: str) -> tuple[bool, int, str]:
    """
    Evaluates whether candidate metadata matches the query.
    Returns: (is_valid, priority_score, reason)
    - priority_score 3: Official Topic channel with matching song & artist
    - priority_score 2: Official Artist channel / VEVO with matching song
    - priority_score 1: Third-party video with strong title & artist token match
    """
    if is_garbage_music_title(title, query):
        return False, 0, "Rejected: contains non-music keyword"

    q_tokens = extract_music_tokens(query)
    if not q_tokens:
        return True, 1, "Generic match"

    cand_text = f"{title} {author}"
    cand_tokens = extract_music_tokens(cand_text)
    overlap = q_tokens.intersection(cand_tokens)

    # Check for official Topic channel (true Song Mode with album artwork)
    is_topic = " - Topic" in author or "Topic" in author
    
    meta = parse_song_meta(query)
    exp_song_tokens = extract_music_tokens(meta.get("song", ""))
    exp_artist_tokens = extract_music_tokens(meta.get("artist", ""))

    if is_topic:
        song_match = not exp_song_tokens or bool(exp_song_tokens.intersection(cand_tokens))
        artist_match = not exp_artist_tokens or bool(exp_artist_tokens.intersection(cand_tokens))
        if song_match and artist_match:
            return True, 3, "Topic channel match"
        if len(overlap) >= len(q_tokens) * 0.5:
            return True, 3, "Topic channel match via token overlap"

    # Non-topic channels:
    if exp_song_tokens and exp_artist_tokens:
        song_overlap = exp_song_tokens.intersection(cand_tokens)
        artist_overlap = exp_artist_tokens.intersection(cand_tokens)
        if song_overlap and artist_overlap:
            author_tokens = extract_music_tokens(author)
            is_artist_channel = bool(exp_artist_tokens.intersection(author_tokens)) or "vevo" in author.lower()
            score = 2 if is_artist_channel else 1
            return True, score, "Both song and artist match"
        return False, 0, f"Missing song or artist match (song={bool(song_overlap)}, artist={bool(artist_overlap)})"

    # General query overlap ratio
    ratio = len(overlap) / len(q_tokens)
    if ratio >= 0.5:
        return True, 1, f"Token overlap ({ratio:.2f})"

    return False, 0, f"Low token overlap ({ratio:.2f})"

def resolve_youtube_music_link(query: str, max_results: int = 1) -> list[dict]:
    """
    Finds verified YouTube Music links for a song query.
    1. Prioritizes free DuckDuckGo search (0 API quota units) targeting official '- Topic' channels.
    2. Strictly filters out YouTube Shorts and non-music garbage (tutorials, reactions, podcasts).
    3. Gracefully falls back to official YouTube Data API v3 ONLY if web search yields no verified match.
    """
    print(f"🎵 [YouTube Music Resolver]: Resolving '{query}'...")
    api_key = os.getenv("YOUTUBE_API_KEY")
    verified_candidates = []
    seen_ids = set()

    # =========================================================================
    # PHASE 1: Free DuckDuckGo Search (0 API Quota Units)
    # =========================================================================
    queries_to_try = [
        f'site:youtube.com "{query}" Topic',
        f'site:music.youtube.com {query}',
        f'site:youtube.com {query}'
    ]
    if " by " in query:
        clean_pair = query.replace(" by ", " ")
        queries_to_try.insert(1, f'site:youtube.com "{clean_pair}" Topic')

    raw_candidates = []
    for q in queries_to_try:
        try:
            for r in list(DDGS().text(q, max_results=max_results + 4)):
                href = r.get('href', '')
                # Fast Shorts rejection on URL structure
                if "/shorts/" in href:
                    continue

                m = re.search(r'(?:v=|\/watch\?v=|\/embed\/|youtu\.be\/)([0-9A-Za-z_-]{11})(?:[?&/]|\b|$)', href)
                if m:
                    vid = m.group(1)
                    if vid not in seen_ids:
                        seen_ids.add(vid)
                        raw_candidates.append((vid, r.get('title', ''), href))
            if len(raw_candidates) >= max_results + 4:
                break
        except Exception:
            continue

    for vid, raw_title, href in raw_candidates:
        # 1. Shorts filter
        if is_youtube_short(vid, href=href, title=raw_title):
            print(f"  └─ ⚠️ [Music Resolver]: Skipping {vid} because it is a YouTube Short.")
            continue

        # 2. Embeddability check
        if not verify_embeddable(vid):
            continue

        # 3. Fetch oEmbed metadata (0 API quota units)
        oembed = get_video_oembed(vid)
        title = oembed.get("title") or raw_title
        author = oembed.get("author", "")
        if not title:
            continue

        # 4. Robust Garbage & Relevance match check
        is_valid, score, reason = score_music_candidate(query, title, author)
        if not is_valid:
            print(f"  └─ ⚠️ [Music Resolver]: Skipping {vid} ('{title}'): {reason}")
            continue

        is_topic = " - Topic" in author or "Topic" in author
        link = f"https://music.youtube.com/watch?v={vid}"
        meta = parse_song_meta(title, author, query=query)

        entry = {
            "id": vid,
            "title": meta["song"],
            "artist": meta["artist"],
            "genre": meta.get("genre"),
            "album": meta.get("album"),
            "url": link,
            "is_topic": is_topic,
            "score": score
        }
        verified_candidates.append(entry)

    # =========================================================================
    # PHASE 2: Fallback to YouTube Data API v3 (Burns 100 quota units ONLY on failure)
    # =========================================================================
    if not verified_candidates and api_key:
        print(f"⚠️ [YouTube Music Resolver]: Web search yielded no verified match. Falling back to YouTube Data API for '{query}'...")
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
                    if not vid or vid in seen_ids:
                        continue
                    seen_ids.add(vid)

                    item_title = item['snippet'].get('title', '')
                    item_channel = item['snippet'].get('channelTitle', '')

                    # Apply Shorts & Garbage filtering to API results as well
                    if is_youtube_short(vid, title=item_title):
                        continue
                    if is_garbage_music_title(item_title, query):
                        continue
                    if not verify_embeddable(vid):
                        continue

                    is_topic = " - Topic" in item_channel or "Topic" in item_channel
                    link = f"https://music.youtube.com/watch?v={vid}"
                    meta = parse_song_meta(item_title, item_channel, query=query)

                    verified_candidates.append({
                        "id": vid,
                        "title": meta["song"],
                        "artist": meta["artist"],
                        "genre": meta.get("genre"),
                        "album": meta.get("album"),
                        "url": link,
                        "is_topic": is_topic,
                        "score": 3 if is_topic else 1
                    })
        except Exception as e:
            print(f"⚠️ [YouTube Music API Fallback Error]: {e}")

    if not verified_candidates:
        return []

    # Sort candidates by match quality score descending (Topic channels first)
    verified_candidates.sort(key=lambda x: x.get("score", 0), reverse=True)

    # Populate in-memory metadata cache for Discord card rendering
    for cand in verified_candidates[:max_results]:
        MUSIC_METADATA_CACHE[cand["id"]] = {
            "song": cand["title"],
            "artist": cand["artist"],
            "genre": cand.get("genre"),
            "album": cand.get("album"),
            "url": cand["url"]
        }

    return verified_candidates[:max_results]
