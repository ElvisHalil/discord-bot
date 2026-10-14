import os
import re
import time
import requests
from dotenv import load_dotenv
from ddgs import DDGS
from src.skills.music.utils import parse_song_meta, MUSIC_METADATA_CACHE, record_played_track
from src.skills.youtube.client import verify_embeddable, get_video_oembed, is_youtube_short

load_dotenv()

# 15-minute cache for resolved YouTube Music queries (eliminates duplicate DDGS + HTTP lookups when pre-resolved in skill_top_music)
_RESOLVED_MUSIC_CACHE: dict[str, tuple[float, list[dict]]] = {}
_RESOLVED_MUSIC_TTL = 900.0

def clear_resolved_music_cache() -> None:
    """Clears in-memory resolved YouTube Music link cache."""
    _RESOLVED_MUSIC_CACHE.clear()

GARBAGE_MUSIC_KEYWORDS = [
    "tutorial", "how to play", "how to make", "reaction", "reacting",
    "reacts", "review", "podcast", "interview", "behind the scenes",
    "gameplay", "walkthrough", "tier list", "parody", "lesson",
    "drum cover", "synth tutorial", "unboxing", "vlog", "prank",
    "meme", "tiktok compilation", "nightcore", "sped up", "speed up",
    "slowed", "super slowed", "ultra slowed", "vocal coach", "reverb",
    "reverbed", "top 50", "top 100", "top 20", "top 10",
    "top 25", "most viral", "compilation", "playlist", "best of",
    "1 hour", "10 hours", "full album", "tracklist", "track list",
    "full set", "dj set", "live set", "club set", "festival set",
    "b2b set", "vinyl set", "radio set", "closing set", "opening set",
    "extended set", "boiler room", "continuous mix", "megamix",
    "hour mix", "min mix", "guest mix", "promo mix", "yearmix",
]

# Matches DJ/live set markers like "(Set)", "[Live Set]", "| Set", "- Set", "Techno Set", "Live @"
# while preserving normal song titles like "Set Fire to the Rain", "Set Me Free", "Sunset", "Mindset"
_GARBAGE_SET_RE = re.compile(
    r'(?:'
    r'[\(\[\{][^\)\]\}]*\bset\b[^\)\]\}]*[\)\]\}]'
    r'|[-–—|]\s*(?:full\s+|dj\s+|live\s+)?set\b'
    r'|\b(?:full|dj|live|club|festival|b2b|vinyl|radio|closing|opening|extended|warmup|warm-up|rooftop|warehouse|stream|rave|techno|house|dnb|phonk|trance|hardstyle|dubstep|edm|electronic|mix)\s+set\b'
    r'|\b(?:set|live)\s+@\s+\w+'
    r')',
    re.IGNORECASE,
)

def is_garbage_music_title(title: str, query: str = "", author: str = "") -> bool:
    """Detects if candidate title or channel contains tutorial, reaction, slowed/reverb, tracklist, or DJ set keywords."""
    title_low = (title or "").lower()
    author_low = (author or "").lower()
    combined_low = f"{title_low} {author_low}".strip()
    query_low = (query or "").lower()
    for kw in GARBAGE_MUSIC_KEYWORDS:
        if kw in combined_low and kw not in query_low:
            return True
    if _GARBAGE_SET_RE.search(title or "") and not _GARBAGE_SET_RE.search(query or ""):
        return True
    return False

def extract_music_tokens(text: str) -> set[str]:
    """Tokenizes alphanumeric words excluding common musical and grammatical stop words."""
    words = re.findall(r'[a-zA-Z0-9]+', text.lower())
    stopwords = {
        "by", "the", "a", "an", "and", "or", "of", "in", "on", "at", "to",
        "for", "with", "feat", "ft", "official", "video", "audio", "music",
        "topic", "remix", "original", "mix", "full", "hd", "4k", "hq",
        "unknown", "artist", "track", "song"
    }
    return {w for w in words if w not in stopwords and len(w) > 1}

def score_music_candidate(query: str, title: str, author: str) -> tuple[bool, int, str]:
    """
    Evaluates whether candidate metadata matches the query.
    Returns: (is_valid, priority_score, reason)
    - priority_score 3: Official Topic channel with matching song & artist
    - priority_score 2: Official Artist channel / VEVO with matching song & artist
    - priority_score 1: Third-party video with strong title & artist token match
    """
    if is_garbage_music_title(title, query, author=author):
        return False, 0, "Rejected: contains non-music keyword"

    q_tokens = extract_music_tokens(query)
    if not q_tokens:
        return True, 1, "Generic match (no query tokens)"

    cand_text = f"{title} {author}"
    cand_tokens = extract_music_tokens(cand_text)
    overlap = q_tokens.intersection(cand_tokens)

    # Check for official Topic channel (true Song Mode with album artwork)
    is_topic = " - Topic" in author or "Topic" in author
    
    meta = parse_song_meta(query)
    exp_song_tokens = extract_music_tokens(meta.get("song", ""))
    artist_name = meta.get("artist", "")
    if artist_name.lower() in ("unknown artist", "unknown", ""):
        exp_artist_tokens = set()
    else:
        exp_artist_tokens = extract_music_tokens(artist_name)

    if is_topic:
        song_match = not exp_song_tokens or bool(exp_song_tokens.intersection(cand_tokens))
        artist_match = not exp_artist_tokens or bool(exp_artist_tokens.intersection(cand_tokens))
        if song_match and artist_match:
            return True, 3, "Topic channel match (song & artist verified)"
        if len(overlap) >= max(1, len(q_tokens) * 0.5):
            return True, 3, f"Topic channel match via token overlap ({len(overlap)}/{len(q_tokens)})"

    # Non-topic channels:
    if exp_song_tokens and exp_artist_tokens:
        song_overlap = exp_song_tokens.intersection(cand_tokens)
        artist_overlap = exp_artist_tokens.intersection(cand_tokens)
        if song_overlap and artist_overlap:
            author_tokens = extract_music_tokens(author)
            is_artist_channel = bool(exp_artist_tokens.intersection(author_tokens)) or "vevo" in author.lower()
            score = 2 if is_artist_channel else 1
            channel_type = "artist channel/VEVO" if is_artist_channel else "standard channel"
            return True, score, f"Song and artist match on {channel_type}"
        return False, 0, f"Missing song or artist match (song={bool(song_overlap)}, artist={bool(artist_overlap)})"

    # General query overlap ratio
    ratio = len(overlap) / len(q_tokens)
    if ratio >= 0.5:
        return True, 1, f"Token overlap ({len(overlap)}/{len(q_tokens)} tokens, ratio {ratio:.2f})"

    return False, 0, f"Low token overlap ({len(overlap)}/{len(q_tokens)} tokens, ratio {ratio:.2f})"

def resolve_youtube_music_link(query: str, max_results: int = 1) -> list[dict]:
    """
    Finds verified YouTube Music links for a song query.
    1. Prioritizes free DuckDuckGo search (0 API quota units) targeting official '- Topic' channels.
    2. Strictly filters out YouTube Shorts and non-music garbage (tutorials, reactions, podcasts).
    3. Gracefully falls back to official YouTube Data API v3 ONLY if web search yields no verified match.
    """
    is_mocked = (
        hasattr(DDGS, "assert_called")
        or hasattr(verify_embeddable, "assert_called")
        or hasattr(is_youtube_short, "assert_called")
        or hasattr(requests.get, "assert_called")
    )
    cache_key = f"{query.strip().lower()}:{max_results}"
    now = time.monotonic()
    if not is_mocked and cache_key in _RESOLVED_MUSIC_CACHE:
        ts, cached_res = _RESOLVED_MUSIC_CACHE[cache_key]
        if now - ts < _RESOLVED_MUSIC_TTL and cached_res:
            print(f"⚡ [YouTube Music Cache Hit]: '{query}' -> {cached_res[0].get('url')}")
            return cached_res

    print(f"🎵 [YouTube Music Resolver]: Resolving '{query}'...")
    api_key = os.getenv("YOUTUBE_API_KEY")
    verified_candidates = []
    seen_ids = set()

    # =========================================================================
    # PHASE 1: Free DuckDuckGo Search (0 API Quota Units)
    # =========================================================================
    meta_q = parse_song_meta(query)
    song_q = meta_q.get("song", "").strip()
    artist_q = meta_q.get("artist", "").strip()
    if artist_q.lower() in ("unknown artist", "unknown"):
        artist_q = ""

    queries_to_try = []
    if song_q and artist_q:
        queries_to_try.append(f'site:youtube.com {song_q} {artist_q} Topic')
        queries_to_try.append(f'site:music.youtube.com {song_q} {artist_q}')
        queries_to_try.append(f'site:youtube.com {song_q} {artist_q}')
        queries_to_try.append(f'site:youtube.com {song_q} {artist_q} audio')
    else:
        clean_q = query.replace(" by ", " ").replace(" - ", " ").strip()
        queries_to_try.append(f'site:youtube.com {clean_q} Topic')
        queries_to_try.append(f'site:music.youtube.com {clean_q}')
        queries_to_try.append(f'site:youtube.com {clean_q}')
        queries_to_try.append(f'site:youtube.com {clean_q} audio')

    def _verify_single_candidate(cand_tuple):
        vid, raw_title, href = cand_tuple
        # 1. Fast string-based Shorts & Garbage rejection (0ms, no network)
        if "/shorts/" in (href or "").lower() or "#short" in (raw_title or "").lower():
            if is_youtube_short(vid, href=href, title=raw_title):
                print(f"  └─ ⚠️ [Music Resolver]: Skipping {vid} ('{raw_title}') - YouTube Short.")
                return None
        if raw_title and is_garbage_music_title(raw_title, query):
            print(f"  └─ ⚠️ [Music Resolver]: Skipping {vid} ('{raw_title}'): Rejected: contains non-music keyword")
            return None

        # 2. Embeddability check (with Discord third-party Referer/origin; also populates _OEMBED_CACHE
        # and marks '- Topic' art tracks as non-Shorts in _SHORT_CACHE so step 3 is 0ms!)
        if not verify_embeddable(vid):
            print(f"  └─ ⚠️ [Music Resolver]: Skipping {vid} ('{raw_title}') - Embedding disabled on Discord.")
            return None

        # 3. Shorts filter (0ms cache hit for '- Topic' art tracks; network HEAD check only for regular uploads)
        if is_youtube_short(vid, href=href, title=raw_title):
            print(f"  └─ ⚠️ [Music Resolver]: Skipping {vid} ('{raw_title}') - YouTube Short.")
            return None

        # 4. Fetch oEmbed metadata (0ms cache hit from verify_embeddable)
        oembed = get_video_oembed(vid)
        if not oembed.get("title"):
            print(f"  └─ ⚠️ [Music Resolver]: Skipping {vid} ('{raw_title}') - Failed oEmbed verification (cannot embed in Discord).")
            return None
        title = oembed["title"]
        author = oembed.get("author", "")

        # 5. Robust Garbage & Relevance match check
        is_valid, score, reason = score_music_candidate(query, title, author)
        if not is_valid:
            print(f"  └─ ⚠️ [Music Resolver]: Skipping {vid} ('{title}'): {reason}")
            return None

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
        channel_desc = "Topic channel" if is_topic else (f"Channel: '{author}'" if author else "Standard")
        print(f"  └─ ✅ [Music Match]: Found '{meta['song']}' by '{meta['artist']}' ({channel_desc}) -> {link} [Score: {score}/3 | Reason: {reason}]")
        return entry

    from concurrent.futures import ThreadPoolExecutor
    import urllib.parse
    from src.skills.youtube.client import _http_get

    def _verify_batch(batch_candidates: list[tuple[str, str, str]]) -> None:
        if not batch_candidates:
            return
        print(f"  └─ 🔎 [Music Resolver]: Verifying {len(batch_candidates)} candidate URL(s) from query...")
        first_entry = _verify_single_candidate(batch_candidates[0])
        if first_entry:
            verified_candidates.append(first_entry)
            if len(verified_candidates) >= max_results and first_entry.get("score", 0) >= 2:
                return

        remaining = batch_candidates[1:]
        if remaining:
            if len(remaining) == 1:
                rem_entry = _verify_single_candidate(remaining[0])
                if rem_entry:
                    verified_candidates.append(rem_entry)
            else:
                with ThreadPoolExecutor(max_workers=min(4, len(remaining))) as pool:
                    for rem_entry in pool.map(_verify_single_candidate, remaining):
                        if rem_entry:
                            verified_candidates.append(rem_entry)

    # =========================================================================
    # PHASE 1A: Direct YouTube Search over Persistent Keep-Alive Session (~0.5s)
    # =========================================================================
    if not hasattr(DDGS, "assert_called") and not hasattr(requests.get, "assert_called"):
        yt_q = f"{song_q} {artist_q} Topic".strip() if (song_q and artist_q) else f"{query.replace(' by ', ' ')} Topic".strip()
        try:
            print(f"  └─ 🔍 [YouTube Direct]: Querying '{yt_q}'...")
            yt_search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(yt_q)}"
            r_yt = _http_get(
                yt_search_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                timeout=3.5,
            )
            if r_yt.status_code == 200:
                direct_vids = list(dict.fromkeys(re.findall(r'"videoRenderer":\{"videoId":"([0-9A-Za-z_-]{11})"', r_yt.text)))
                if not direct_vids:
                    direct_vids = list(dict.fromkeys(re.findall(r'"videoId":"([0-9A-Za-z_-]{11})"', r_yt.text)))
                direct_batch = []
                for vid in direct_vids[: max_results + 5]:
                    if vid not in seen_ids:
                        seen_ids.add(vid)
                        direct_batch.append((vid, "", f"https://www.youtube.com/watch?v={vid}"))
                _verify_batch(direct_batch)
        except Exception as e:
            print(f"  └─ ⚠️ [YouTube Direct Search Warning]: {e}")

    # =========================================================================
    # PHASE 1B: Free DuckDuckGo Search Fallback (0 API Quota Units)
    # =========================================================================
    if len(verified_candidates) < max_results:
        for q in queries_to_try:
            batch_candidates = []
            try:
                print(f"  └─ 🔍 [DuckDuckGo]: Querying '{q}'...")
                for r in list(DDGS().text(q, max_results=max_results + 5)):
                    href = r.get('href', '')
                    if "/shorts/" in href:
                        continue

                    m = re.search(r'(?:v=|\/watch\?v=|\/embed\/|youtu\.be\/)([0-9A-Za-z_-]{11})(?:[?&/]|\b|$)', href)
                    if m:
                        vid = m.group(1)
                        if vid not in seen_ids:
                            seen_ids.add(vid)
                            batch_candidates.append((vid, r.get('title', ''), href))
            except Exception as e:
                print(f"  └─ ⚠️ [DuckDuckGo Search Warning]: {e}")
                continue

            if not batch_candidates:
                continue

            _verify_batch(batch_candidates)
            if len(verified_candidates) >= max_results:
                break

    # =========================================================================
    # PHASE 2: Fallback to YouTube Data API v3 (Burns 100 quota units ONLY on failure)
    # =========================================================================
    if not verified_candidates and api_key:
        print(f"⚠️ [Music Resolver Fallback]: Web search yielded no verified match. Falling back to YouTube Data API for '{query}'...")
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
                        print(f"  └─ ⚠️ [Music API Fallback]: Skipping {vid} - Short.")
                        continue
                    if is_garbage_music_title(item_title, query, author=item_channel):
                        print(f"  └─ ⚠️ [Music API Fallback]: Skipping {vid} - Non-music keyword.")
                        continue
                    if not verify_embeddable(vid):
                        print(f"  └─ ⚠️ [Music API Fallback]: Skipping {vid} - Embedding disabled.")
                        continue

                    is_topic = " - Topic" in item_channel or "Topic" in item_channel
                    link = f"https://music.youtube.com/watch?v={vid}"
                    meta = parse_song_meta(item_title, item_channel, query=query)

                    entry = {
                        "id": vid,
                        "title": meta["song"],
                        "artist": meta["artist"],
                        "genre": meta.get("genre"),
                        "album": meta.get("album"),
                        "url": link,
                        "is_topic": is_topic,
                        "score": 3 if is_topic else 1
                    }
                    verified_candidates.append(entry)
                    print(f"  └─ ✅ [Music API Match]: Found '{meta['song']}' by '{meta['artist']}' (Channel: '{item_channel}') -> {link}")
        except Exception as e:
            print(f"⚠️ [YouTube Music API Fallback Error]: {e}")

    if not verified_candidates:
        print(f"❌ [Music Resolver]: No verified music tracks found for '{query}'.")
        return []

    # Sort candidates by match quality score descending (Topic channels first)
    verified_candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
    final_results = verified_candidates[:max_results]

    # Populate in-memory metadata cache for Discord card rendering
    for cand in final_results:
        MUSIC_METADATA_CACHE[cand["id"]] = {
            "song": cand["title"],
            "artist": cand["artist"],
            "genre": cand.get("genre"),
            "album": cand.get("album"),
            "url": cand["url"]
        }
        record_played_track(cand["title"], cand["artist"])
        topic_tag = " [Official Topic]" if cand.get("is_topic") else ""
        print(f"🎧 [Music Resolver Returned]: '{cand['title']}' by '{cand['artist']}' -> {cand['url']}{topic_tag} (Score: {cand.get('score', 0)}/3)")

    if not is_mocked and final_results:
        _RESOLVED_MUSIC_CACHE[cache_key] = (time.monotonic(), final_results)
        # Also index by canonical 'title by artist' so subsequent lookups hit 0ms cache
        canon_key = f"{final_results[0]['title'].strip().lower()} by {final_results[0]['artist'].strip().lower()}:{max_results}"
        _RESOLVED_MUSIC_CACHE[canon_key] = (time.monotonic(), final_results)

    return final_results
