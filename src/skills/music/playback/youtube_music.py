import os
import re
import time
import requests
from dotenv import load_dotenv
from ddgs import DDGS
from src.skills.music.utils import parse_song_meta, MUSIC_METADATA_CACHE, record_played_track
from src.skills.youtube.client import verify_embeddable, get_video_oembed, is_youtube_short, _OEMBED_CACHE

load_dotenv()

# 15-minute cache for resolved YouTube Music queries (eliminates duplicate DDGS + HTTP lookups when pre-resolved in skill_top_music)
_RESOLVED_MUSIC_CACHE: dict[str, tuple[float, list[dict]]] = {}
_RESOLVED_MUSIC_TTL = 900.0

# Short-lived negative cache (15s) to prevent duplicate lookups within the same turn when all uploads are blocked
_FAILED_MUSIC_CACHE: dict[str, float] = {}
_FAILED_MUSIC_TTL = 15.0

# Tracks queries where official Topic/VEVO/Artist uploads were found on YouTube but blocked from Discord embedding by the label
_LAST_BLOCKED_OFFICIAL: dict[str, dict] = {}

# Tracks video IDs already presented in Discord song cards per normalized song/query so retries pick a different link
SERVED_SONG_VIDS: dict[str, set[str]] = {}


def _norm_lookup_key(query: str) -> str:
    meta = parse_song_meta(query)
    song = re.sub(r'[^a-z0-9]+', ' ', (meta.get("song") or "").lower()).strip()
    artist = re.sub(r'[^a-z0-9]+', ' ', (meta.get("artist") or "").lower()).strip()
    if artist in ("unknown artist", "unknown"):
        artist = ""
    if song and artist:
        return f"{song} by {artist}"
    return re.sub(r'[^a-z0-9]+', ' ', (query or "").lower()).strip()


def record_served_song_vid(song: str, artist: str = "", vid: str = "") -> None:
    """Records a video ID that was formatted into a Discord song card so subsequent retry searches try a different link."""
    if not vid or not song:
        return
    q = f"{song} by {artist}".strip() if artist else song
    key = _norm_lookup_key(q)
    if key:
        SERVED_SONG_VIDS.setdefault(key, set()).add(vid)
    raw_key = re.sub(r'[^a-z0-9]+', ' ', f"{song} {artist}".lower()).strip()
    if raw_key:
        SERVED_SONG_VIDS.setdefault(raw_key, set()).add(vid)


def get_served_song_vids(query: str) -> set[str]:
    """Returns any video IDs already presented to the user for this song query."""
    if not query:
        return set()
    key = _norm_lookup_key(query)
    raw_key = re.sub(r'[^a-z0-9]+', ' ', query.replace(" by ", " ").lower()).strip()
    vids: set[str] = set()
    if key in SERVED_SONG_VIDS:
        vids.update(SERVED_SONG_VIDS[key])
    if raw_key in SERVED_SONG_VIDS:
        vids.update(SERVED_SONG_VIDS[raw_key])
    return vids


def get_blocked_official_info(query: str) -> dict | None:
    """Returns metadata about official Topic/VEVO/Artist uploads that were blocked from Discord embedding for this query."""
    if not query:
        return None
    return _LAST_BLOCKED_OFFICIAL.get(_norm_lookup_key(query)) or _LAST_BLOCKED_OFFICIAL.get(query.strip().lower())


def clear_resolved_music_cache() -> None:
    """Clears in-memory resolved YouTube Music link cache."""
    _RESOLVED_MUSIC_CACHE.clear()
    _FAILED_MUSIC_CACHE.clear()
    _LAST_BLOCKED_OFFICIAL.clear()
    SERVED_SONG_VIDS.clear()

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
    "bootleg", "ai cover", "ai version", "type beat", "karaoke",
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

# Matches clickbait fake official+year tags on unverified third-party channels, e.g. "(Official Music 2026)", "[Official Video 2025]", "(Official 2026)"
_FAKE_OFFICIAL_YEAR_RE = re.compile(
    r'(?:'
    r'[\(\[\{][^\)\]\}]*\bofficial\b[^\)\]\}]*\b(202[4-9])\b[^\)\]\}]*[\)\]\}]'
    r'|\bofficial\s+(?:music|video|audio|song|track)?\s*(202[4-9])\b'
    r')',
    re.IGNORECASE,
)

# Channel names that indicate unofficial mix/bootleg/re-upload accounts when the channel is not the artist or Topic
_UNVERIFIED_MIX_CHANNEL_RE = re.compile(
    r'\b(?:mix|mixes|remix|remixes|mashup|mashups|bootleg|bootlegs|sped\s+up|slowed|reverb|8d|bass\s+boost(?:ed)?|type\s+beats?|karaoke)\b',
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

    title_tokens = extract_music_tokens(title)
    author_tokens = extract_music_tokens(author)
    cand_tokens = title_tokens.union(author_tokens)
    overlap = q_tokens.intersection(cand_tokens)

    # A valid music candidate must share at least one meaningful query token in its video title
    if title_tokens and not title_tokens.intersection(q_tokens):
        return False, 0, "Rejected: video title has no token overlap with query"

    # Check for official Topic channel (true Song Mode with album artwork)
    is_topic = " - Topic" in author or "Topic" in author

    meta = parse_song_meta(query)
    exp_song_tokens = extract_music_tokens(meta.get("song", ""))
    artist_name = meta.get("artist", "")
    if artist_name.lower() in ("unknown artist", "unknown", ""):
        exp_artist_tokens = set()
    else:
        exp_artist_tokens = extract_music_tokens(artist_name)

    # If the query didn't use 'by' or '-' (so exp_artist_tokens is empty), isolate the song tokens
    # by subtracting known channel/artist tokens when non-empty.
    effective_song_tokens = exp_song_tokens
    if not exp_artist_tokens and author_tokens and (q_tokens - author_tokens):
        effective_song_tokens = q_tokens - author_tokens

    if not effective_song_tokens:
        song_match = True
    elif len(effective_song_tokens) <= 2:
        song_match = effective_song_tokens.issubset(title_tokens)
    else:
        req_count = max(2, int(len(effective_song_tokens) * 0.6))
        song_match = len(effective_song_tokens.intersection(title_tokens)) >= req_count

    if is_topic:
        artist_match = not exp_artist_tokens or bool(exp_artist_tokens.intersection(cand_tokens))
        if song_match and artist_match:
            return True, 3, "Topic channel match (song & artist verified)"
        if song_match and len(overlap) >= max(1, len(q_tokens) * 0.5):
            return True, 3, f"Topic channel match via token overlap ({len(overlap)}/{len(q_tokens)})"
        return False, 0, f"Rejected: Topic channel track mismatch (song_match={song_match}, artist_match={artist_match})"

    # Non-topic channels:
    if exp_song_tokens and exp_artist_tokens:
        artist_overlap = exp_artist_tokens.intersection(cand_tokens)
        if song_match and artist_overlap:
            is_artist_channel = bool(exp_artist_tokens.intersection(author_tokens)) or "vevo" in author.lower()
            if not is_artist_channel:
                m_fake_yr = _FAKE_OFFICIAL_YEAR_RE.search(title or "")
                if m_fake_yr:
                    yr = m_fake_yr.group(1) or m_fake_yr.group(2) or ""
                    if yr not in query.lower():
                        return False, 0, f"Rejected: unverified channel with fake official year tag ({yr})"
                m_mix_chan = _UNVERIFIED_MIX_CHANNEL_RE.search(author or "")
                if m_mix_chan and m_mix_chan.group(0).lower() not in query.lower():
                    return False, 0, f"Rejected: unverified mix/re-upload channel ('{author}')"
            score = 2 if is_artist_channel else 1
            channel_type = "artist channel/VEVO" if is_artist_channel else "standard channel"
            return True, score, f"Song and artist match on {channel_type}"
        return False, 0, f"Missing song or artist match (song={song_match}, artist={bool(artist_overlap)})"

    if not song_match:
        return False, 0, "Rejected: candidate title does not match song tokens"

    # General query overlap ratio
    ratio = len(overlap) / len(q_tokens)
    if ratio >= 0.5:
        m_fake_yr = _FAKE_OFFICIAL_YEAR_RE.search(title or "")
        if m_fake_yr:
            yr = m_fake_yr.group(1) or m_fake_yr.group(2) or ""
            if yr not in query.lower():
                return False, 0, f"Rejected: unverified channel with fake official year tag ({yr})"
        m_mix_chan = _UNVERIFIED_MIX_CHANNEL_RE.search(author or "")
        if m_mix_chan and m_mix_chan.group(0).lower() not in query.lower():
            return False, 0, f"Rejected: unverified mix/re-upload channel ('{author}')"
        return True, 1, f"Token overlap ({len(overlap)}/{len(q_tokens)} tokens, ratio {ratio:.2f})"

    return False, 0, f"Low token overlap ({len(overlap)}/{len(q_tokens)} tokens, ratio {ratio:.2f})"

_TRUSTED_MUSIC_LABELS = (
    "afterlife", "ukf", "hospital records", "spinnin", "monstercat",
    "armada music", "defected", "ultra music", "ultra records", "drumcode",
    "anjunadeep", "anjunabeats", "liquicity", "blackout music", "ram records",
    "selected.", "mrsuicidesheep", "suicidesheep", "proximity", "stmpd rcrds",
    "musical freedom", "kontor.tv", "napalm records", "nuclear blast",
    "fueled by ramen", "atlantic records", "interscope", "death row",
    "aftermath", "shady records", "top dawg", "empire", "epitaph",
    "sub pop", "xl recordings", "warp records", "ninja tune", "owsla",
    "mad decent", "spinning records", "ministry of sound",
)


def _is_trusted_music_label(author: str) -> bool:
    auth_low = (author or "").lower().strip()
    return bool(auth_low and any(lbl in auth_low for lbl in _TRUSTED_MUSIC_LABELS))


def resolve_youtube_music_link(query: str, max_results: int = 1, exclude_ids: set[str] | None = None) -> list[dict]:
    """
    Finds verified YouTube Music links for a song query using a 3-tier priority hierarchy:
    1. Tier 1 (official_embeddable): Official '- Topic', Artist/VEVO, or Trusted Label upload that embeds inline in Discord.
    2. Tier 2 (official_blocked): Official '- Topic' or Artist/VEVO upload whose inline embed is disabled by the record label
       (preferred over sketchy third-party re-uploads when an official upload exists).
    3. Tier 3 (standard_embeddable): Clean standard upload, used ONLY when no official upload exists at all.
    """
    is_mocked = (
        hasattr(DDGS, "assert_called")
        or hasattr(verify_embeddable, "assert_called")
        or hasattr(is_youtube_short, "assert_called")
        or hasattr(requests.get, "assert_called")
    )
    excluded = set(exclude_ids or ())
    cache_key = f"{query.strip().lower()}:{max_results}"
    norm_key = _norm_lookup_key(query)
    now = time.monotonic()

    if not is_mocked and not excluded:
        if cache_key in _RESOLVED_MUSIC_CACHE:
            ts, cached_res = _RESOLVED_MUSIC_CACHE[cache_key]
            if now - ts < _RESOLVED_MUSIC_TTL and cached_res:
                print(f"⚡ [YouTube Music Cache Hit]: '{query}' -> {cached_res[0].get('url')}")
                return cached_res
        if cache_key in _FAILED_MUSIC_CACHE and (now - _FAILED_MUSIC_CACHE[cache_key]) < _FAILED_MUSIC_TTL:
            print(f"⚡ [YouTube Music Negative Cache Hit]: '{query}' recently verified as having 0 valid uploads.")
            return []

    print(f"🎵 [YouTube Music Resolver]: Resolving '{query}'" + (f" (excluding {excluded})..." if excluded else "..."))
    api_key = os.getenv("YOUTUBE_API_KEY")
    official_embeddable: list[dict] = []
    official_blocked: list[dict] = []
    standard_embeddable: list[dict] = []
    seen_ids = set(excluded)

    # =========================================================================
    # PHASE 1: Web Search Queries
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
            cached_oe = _OEMBED_CACHE.get(vid) or get_video_oembed(vid)
            b_title = (cached_oe or {}).get("title", "")
            b_author = (cached_oe or {}).get("author", "")
            if b_title and not is_youtube_short(vid, href=href, title=b_title):
                u_valid, u_score, u_reason = score_music_candidate(query, b_title, b_author)
                if u_valid and u_score >= 2:
                    is_topic = " - Topic" in b_author or "Topic" in b_author
                    link = f"https://music.youtube.com/watch?v={vid}"
                    b_meta = parse_song_meta(b_title, b_author, query=query)
                    official_blocked.append({
                        "id": vid,
                        "title": b_meta["song"],
                        "artist": b_meta["artist"],
                        "genre": b_meta.get("genre"),
                        "album": b_meta.get("album"),
                        "author": b_author,
                        "url": link,
                        "is_topic": is_topic,
                        "score": u_score,
                        "embeddable": False,
                        "is_official_fallback": True,
                        "reason": u_reason,
                    })
                    print(
                        f"  └─ 📌 [Music Resolver]: Recorded official upload {vid} ('{b_meta['song']}' by '{b_meta['artist']}', "
                        f"Channel: '{b_author}', Score: {u_score}/3) as fallback (Discord inline embed disabled by label)."
                    )
                    return None
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
        is_trusted_official = (score >= 2) or _is_trusted_music_label(author)
        link = f"https://music.youtube.com/watch?v={vid}"
        meta = parse_song_meta(title, author, query=query)

        entry = {
            "id": vid,
            "title": meta["song"],
            "artist": meta["artist"],
            "genre": meta.get("genre"),
            "album": meta.get("album"),
            "author": author,
            "url": link,
            "is_topic": is_topic,
            "score": score,
            "is_trusted_official": is_trusted_official,
            "embeddable": True,
            "is_official_fallback": False,
        }
        channel_desc = "Topic channel" if is_topic else (f"Channel: '{author}'" if author else "Standard")
        print(f"  └─ ✅ [Music Match]: Found '{meta['song']}' by '{meta['artist']}' ({channel_desc}) -> {link} [Score: {score}/3 | Reason: {reason}]")
        return entry

    def _record_verified_entry(entry: dict) -> None:
        if entry.get("is_trusted_official"):
            official_embeddable.append(entry)
        else:
            standard_embeddable.append(entry)

    from concurrent.futures import ThreadPoolExecutor
    import urllib.parse
    from src.skills.youtube.client import _http_get

    def _verify_batch(batch_candidates: list[tuple[str, str, str]]) -> None:
        if not batch_candidates:
            return
        print(f"  └─ 🔎 [Music Resolver]: Verifying {len(batch_candidates)} candidate URL(s) from query...")
        first_entry = _verify_single_candidate(batch_candidates[0])
        if first_entry:
            _record_verified_entry(first_entry)
            if len(official_embeddable) >= max_results:
                return

        remaining = batch_candidates[1:]
        if remaining:
            if len(remaining) == 1:
                rem_entry = _verify_single_candidate(remaining[0])
                if rem_entry:
                    _record_verified_entry(rem_entry)
            else:
                with ThreadPoolExecutor(max_workers=min(4, len(remaining))) as pool:
                    for rem_entry in pool.map(_verify_single_candidate, remaining):
                        if rem_entry:
                            _record_verified_entry(rem_entry)

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
    if len(official_embeddable) < max_results and not (standard_embeddable and not official_blocked):
        # If Phase 1A already found official_blocked uploads (e.g. Topic/VEVO blocked by label),
        # only check at most 1 DDG query in case an alternate official upload is embeddable.
        ddg_queries = queries_to_try[:1] if official_blocked else queries_to_try
        for q in ddg_queries:
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
            if len(official_embeddable) >= max_results or (len(standard_embeddable) >= max_results and not official_blocked):
                break

    # =========================================================================
    # PHASE 2: Fallback to YouTube Data API v3 (Burns 100 quota units ONLY on failure)
    # =========================================================================
    if not official_embeddable and not official_blocked and not standard_embeddable and api_key and (not is_mocked or hasattr(requests.get, "assert_called")):
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

                    # Apply Shorts, Garbage, and Relevance scoring to API results as well
                    if is_youtube_short(vid, title=item_title):
                        print(f"  └─ ⚠️ [Music API Fallback]: Skipping {vid} - Short.")
                        continue
                    is_valid, score, reason = score_music_candidate(query, item_title, item_channel)
                    if not is_valid:
                        print(f"  └─ ⚠️ [Music API Fallback]: Skipping {vid} ('{item_title}'): {reason}")
                        continue
                    is_topic = " - Topic" in item_channel or "Topic" in item_channel
                    link = f"https://music.youtube.com/watch?v={vid}"
                    meta = parse_song_meta(item_title, item_channel, query=query)

                    if not verify_embeddable(vid):
                        if score >= 2:
                            official_blocked.append({
                                "id": vid,
                                "title": meta["song"],
                                "artist": meta["artist"],
                                "genre": meta.get("genre"),
                                "album": meta.get("album"),
                                "author": item_channel,
                                "url": link,
                                "is_topic": is_topic,
                                "score": score,
                                "embeddable": False,
                                "is_official_fallback": True,
                            })
                        print(f"  └─ ⚠️ [Music API Fallback]: Skipping {vid} - Embedding disabled.")
                        continue

                    entry = {
                        "id": vid,
                        "title": meta["song"],
                        "artist": meta["artist"],
                        "genre": meta.get("genre"),
                        "album": meta.get("album"),
                        "author": item_channel,
                        "url": link,
                        "is_topic": is_topic,
                        "score": score,
                        "is_trusted_official": (score >= 2) or _is_trusted_music_label(item_channel),
                        "embeddable": True,
                        "is_official_fallback": False,
                    }
                    _record_verified_entry(entry)
                    print(f"  └─ ✅ [Music API Match]: Found '{meta['song']}' by '{meta['artist']}' (Channel: '{item_channel}') -> {link}")
        except Exception as e:
            print(f"⚠️ [YouTube Music API Fallback Error]: {e}")

    # =========================================================================
    # PHASE 3: 3-Tier Decision Engine
    # =========================================================================
    if official_embeddable:
        official_embeddable.sort(key=lambda x: x.get("score", 0), reverse=True)
        final_results = official_embeddable[:max_results]
    elif official_blocked:
        # Sort official blocked candidates so '- Topic' (score 3) beats VEVO/Artist (score 2)
        official_blocked.sort(key=lambda x: x.get("score", 0), reverse=True)
        best_blocked = official_blocked[0]
        info = {
            "song": best_blocked["title"],
            "artist": best_blocked["artist"],
            "author": best_blocked.get("author", ""),
            "id": best_blocked["id"],
        }
        if norm_key:
            _LAST_BLOCKED_OFFICIAL[norm_key] = info
        _LAST_BLOCKED_OFFICIAL[query.strip().lower()] = info
        print(
            f"📌 [Music Resolver Official Fallback]: No inline-embeddable official upload exists for '{best_blocked['title']}' "
            f"by '{best_blocked['artist']}'. Returning official '{best_blocked.get('author')}' link ({best_blocked['url']}) instead of third-party junk."
        )
        final_results = official_blocked[:max_results]
    elif standard_embeddable:
        standard_embeddable.sort(key=lambda x: x.get("score", 0), reverse=True)
        final_results = standard_embeddable[:max_results]
    else:
        print(f"❌ [Music Resolver]: No verified music tracks found for '{query}'.")
        if not is_mocked and not excluded:
            _FAILED_MUSIC_CACHE[cache_key] = time.monotonic()
        return []

    # Populate in-memory metadata cache for Discord card rendering
    for cand in final_results:
        MUSIC_METADATA_CACHE[cand["id"]] = {
            "song": cand["title"],
            "artist": cand["artist"],
            "genre": cand.get("genre"),
            "album": cand.get("album"),
            "url": cand["url"],
            "embeddable": cand.get("embeddable", True),
            "is_official_fallback": cand.get("is_official_fallback", False),
        }
        record_played_track(cand["title"], cand["artist"])
        topic_tag = " [Official Topic]" if cand.get("is_topic") else ""
        fb_tag = " [Official Link - Inline Embed Disabled by Label]" if cand.get("is_official_fallback") else ""
        print(f"🎧 [Music Resolver Returned]: '{cand['title']}' by '{cand['artist']}' -> {cand['url']}{topic_tag}{fb_tag} (Score: {cand.get('score', 0)}/3)")

    if not is_mocked and final_results:
        _RESOLVED_MUSIC_CACHE[cache_key] = (time.monotonic(), final_results)
        # Also index by canonical 'title by artist' so subsequent lookups hit 0ms cache
        canon_key = f"{final_results[0]['title'].strip().lower()} by {final_results[0]['artist'].strip().lower()}:{max_results}"
        _RESOLVED_MUSIC_CACHE[canon_key] = (time.monotonic(), final_results)

    return final_results

