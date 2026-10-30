import os
import re
import time
import random
import requests
from requests.adapters import HTTPAdapter
from dotenv import load_dotenv
from src.skills.music.aggregators.base import BaseMusicAggregator
from src.skills.music.models import Track
from src.skills.music.engine.formatting import parse_song_meta
from src.utils.http import redact_secrets
from src.utils.workers import map_in_workers, submit_to_worker

load_dotenv()

_HTTP_SESSION = requests.Session()
_adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20)
_HTTP_SESSION.mount("https://", _adapter)
_HTTP_SESSION.mount("http://", _adapter)

def _http_get(url: str, **kwargs):
    return _HTTP_SESSION.get(url, **kwargs)

_LASTFM_RAW_CACHE: dict[str, tuple[float, dict]] = {}
_LASTFM_VERIFIED_ARTISTS_CACHE: dict[str, set[str]] = {}
_LASTFM_CACHE_TTL = 900.0

_ALBUM_FILLER_RE = re.compile(
    r"(?:^|\b)(?:intro|outro|interlude|skit|prologue|epilogue|commentary|voice\s*memo)(?:\b|$)",
    re.IGNORECASE,
)

_LASTFM_CANONICAL_TAGS: dict[str, str] = {
    "liquid drum and bass": "liquid funk",
    "liquid drum & bass": "liquid funk",
    "liquid dnb": "liquid funk",
    "melodic drum and bass": "liquid funk",
    "melodic dnb": "liquid funk",
    "dnb": "drum and bass",
    "drum & bass": "drum and bass",
    "drumnbass": "drum and bass",
    "r&b": "rnb",
    "r and b": "rnb",
}


def _canonicalize_lastfm_tag(raw_genre: str | None) -> str:
    if not raw_genre:
        return ""
    low = raw_genre.lower().strip()
    return _LASTFM_CANONICAL_TAGS.get(low, low)


def clear_lastfm_cache() -> None:
    _LASTFM_RAW_CACHE.clear()
    _LASTFM_VERIFIED_ARTISTS_CACHE.clear()

class LastFmAggregator(BaseMusicAggregator):
    """
    Music data and recommendation provider utilizing the Last.fm Web Services API v2.0.
    Leverages scrobble graph intelligence for track similarity, artist similarity,
    and global/genre top charts.
    """
    BASE_URL = "https://ws.audioscrobbler.com/2.0/"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("LASTFM_API_KEY")

    def _call(self, method: str, params: dict) -> dict:
        """Helper to invoke Last.fm REST API with JSON format, 15-min raw TTL caching, and error handling."""
        key = self.api_key or os.getenv("LASTFM_API_KEY")
        if not key:
            return {}
        cache_key = f"{method}:" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
        now = time.monotonic()
        if cache_key in _LASTFM_RAW_CACHE:
            ts, cached_data = _LASTFM_RAW_CACHE[cache_key]
            if now - ts < _LASTFM_CACHE_TTL:
                return cached_data

        p = {
            "method": method,
            "api_key": key,
            "format": "json",
            **params
        }
        try:
            r = _http_get(self.BASE_URL, params=p, timeout=5, headers={"User-Agent": "DiscordMusicBot/1.0"})
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and data:
                    _LASTFM_RAW_CACHE[cache_key] = (now, data)
                return data
            else:
                print(f"  └─ ⚠️ [Last.fm]: API returned status {r.status_code} for method '{method}'")
        except Exception as e:
            print(f"  └─ ⚠️ [Last.fm Error]: {redact_secrets(e)}")
        return {}

    def _parse_similar_tracks(self, data: dict, exclude_song: str = "") -> list[Track]:
        """Extracts Track models from a Last.fm similartracks response, enforcing a relevance floor
        and organizing matches into 3 shelves (Tier 1: Closest, Tier 2: Strong Neighbors, Tier 3: Deeper Cuts)."""
        tracks = []
        raw = data.get("similartracks", {}).get("track", [])
        if isinstance(raw, dict):
            raw = [raw]
        for idx, t in enumerate(raw):
            title = t.get("name", "")
            artist_obj = t.get("artist", {})
            artist = artist_obj.get("name", "") if isinstance(artist_obj, dict) else str(artist_obj)
            if not title or not artist:
                continue
            if exclude_song and exclude_song.lower() == title.lower():
                continue
            if _ALBUM_FILLER_RE.search(title) and not _ALBUM_FILLER_RE.search(exclude_song or ""):
                continue

            raw_match = t.get("match")
            if raw_match is not None:
                try:
                    match_score = float(raw_match)
                except (TypeError, ValueError):
                    match_score = max(0.05, 1.0 - idx * 0.02)
            else:
                match_score = max(0.05, 1.0 - idx * 0.02)

            # Relevance floor: drop weak #23-#50 tail tracks or ultra-low similarity matches (<0.15 once core pool is filled)
            if len(tracks) >= 22:
                break
            if raw_match is not None and match_score < 0.15 and len(tracks) >= 6:
                continue

            if len(tracks) < 8 or (raw_match is not None and match_score >= 0.55):
                tier = 1
            elif len(tracks) < 16 or (raw_match is not None and match_score >= 0.28):
                tier = 2
            else:
                tier = 3

            tracks.append(Track(
                title=title,
                artist=artist,
                source="lastfm_similar",
                tier=tier,
                relevance_score=round(match_score, 3),
                verified_genre=True,
            ))
        return tracks

    def _is_relevant_match(self, query: str, expected_artist: str, cand_artist: str, cand_track: str, expected_track: str = "") -> bool:
        """
        Ensures a canonical search match is legitimately related to the query to prevent genre hijacking.
        Strictly prevents cross-role contamination where song title words match an unrelated band name.
        """
        if not cand_artist or not cand_track:
            return False

        q_tokens = {w for w in re.findall(r'[a-zA-Z0-9]+', query.lower()) if len(w) > 1}
        cand_trk_tokens = {w for w in re.findall(r'[a-zA-Z0-9]+', cand_track.lower()) if len(w) > 1}
        cand_art_tokens = {w for w in re.findall(r'[a-zA-Z0-9]+', cand_artist.lower()) if len(w) > 1}

        # Case 1: Expected artist is known
        if expected_artist:
            exp_art_tokens = {w for w in re.findall(r'[a-zA-Z0-9]+', expected_artist.lower()) if len(w) > 1}
            if exp_art_tokens:
                # Require artist token overlap
                art_overlap = exp_art_tokens.intersection(cand_art_tokens)
                if not art_overlap:
                    return False
            # If expected track is also known, require track overlap
            if expected_track:
                exp_trk_tokens = {w for w in re.findall(r'[a-zA-Z0-9]+', expected_track.lower()) if len(w) > 1}
                if exp_trk_tokens and not exp_trk_tokens.intersection(cand_trk_tokens):
                    return False
            return True

        # Case 2: Expected artist is not known (free-form query)
        if not cand_trk_tokens:
            return False

        trk_in_q = cand_trk_tokens.intersection(q_tokens)
        if not trk_in_q:
            return False

        # Precision of candidate track tokens: at least 60% of the candidate track's words must be in the query
        trk_precision = len(trk_in_q) / len(cand_trk_tokens)
        if trk_precision < 0.6:
            return False

        # Check remaining query words not covered by candidate track
        remaining_q = q_tokens - trk_in_q
        if not remaining_q:
            return True

        # If 2 or more words in query remain, they likely indicate a specific artist.
        # If candidate artist has 0 overlap with those remaining words, reject as wrong artist.
        art_in_rem = cand_art_tokens.intersection(remaining_q)
        if len(remaining_q) >= 2 and not art_in_rem:
            return False

        # Total overlap between query and (cand_track + cand_artist) must cover at least 60% of query
        total_overlap = trk_in_q.union(cand_art_tokens.intersection(q_tokens))
        return len(total_overlap) >= max(1, int(len(q_tokens) * 0.6))

    def _get_similar_artists_tracks(self, artist: str, max_artists: int = 8, tracks_per_artist: int = 4) -> list[Track]:
        """Fetches top tracks from closely related artists on Last.fm's artist similarity graph,
        tagged into 3 shelves (Tier 1: Top closest artists, Tier 2: Mid-catalog cuts, Tier 3: Deeper scene cuts)."""
        tracks = []
        artist_res = self._call("artist.getsimilar", {
            "artist": artist,
            "limit": max_artists + 4,
            "autocorrect": 1
        })
        similar_artists = artist_res.get("similarartists", {}).get("artist", [])
        if isinstance(similar_artists, dict):
            similar_artists = [similar_artists]

        rel_items: list[tuple[int, str]] = []
        for idx, sa in enumerate(similar_artists):
            if len(rel_items) >= max_artists:
                break
            name = sa.get("name", "") if isinstance(sa, dict) else str(sa)
            if not name:
                continue
            raw_m = sa.get("match") if isinstance(sa, dict) else None
            if raw_m is not None:
                try:
                    if float(raw_m) < 0.20 and len(rel_items) >= 4:
                        continue
                except (TypeError, ValueError):
                    pass
            rel_items.append((idx, name))

        if not rel_items:
            return []

        def _fetch_artist_top(item: tuple[int, str]) -> list[Track]:
            art_idx, rel_name = item
            local_tracks = []
            top_res = self._call("artist.gettoptracks", {
                "artist": rel_name,
                "limit": max(tracks_per_artist, 8),
                "autocorrect": 1
            })
            top_tracks = top_res.get("toptracks", {}).get("track", [])
            if isinstance(top_tracks, dict):
                top_tracks = [top_tracks]
            clean_top = [
                tt for tt in top_tracks
                if tt.get("name", "") and not _ALBUM_FILLER_RE.search(tt.get("name", ""))
            ] or top_tracks
            for t_idx, tt in enumerate(clean_top[:tracks_per_artist]):
                t_name = tt.get("name", "")
                if t_name:
                    if art_idx < 3 and t_idx < 2:
                        tier = 1
                        rel_score = 0.90
                    elif art_idx < 6 and t_idx < 3:
                        tier = 2
                        rel_score = 0.78
                    else:
                        tier = 3
                        rel_score = 0.65
                    local_tracks.append(Track(
                        title=t_name,
                        artist=rel_name,
                        source="lastfm_similar",
                        tier=tier,
                        relevance_score=rel_score,
                        verified_genre=True,
                    ))
            return local_tracks

        for art_tracks in map_in_workers(_fetch_artist_top, rel_items):
            tracks.extend(art_tracks)
        return tracks

    def get_similar(self, query: str) -> list[Track]:
        key = self.api_key or os.getenv("LASTFM_API_KEY")
        if not key:
            print("  └─ ⚠️ [Last.fm]: LASTFM_API_KEY is not configured in .env")
            return []

        print(f"  └─ 🔍 [Last.fm]: Finding similar tracks for '{query}'...")
        tracks = []
        meta = parse_song_meta(query)
        song_title = meta.get("song", "").strip()
        artist_name = meta.get("artist", "").strip()
        if artist_name.lower() in ("unknown artist", "unknown", "release", "various artists"):
            artist_name = ""

        # Strategy 1: Direct track.getSimilar if both song and artist were cleanly parsed
        if song_title and artist_name:
            res = self._call("track.getsimilar", {
                "artist": artist_name,
                "track": song_title,
                "limit": 50,
                "autocorrect": 1
            })
            tracks = self._parse_similar_tracks(res, exclude_song=song_title)
            if tracks:
                print(f"  └─ ✅ [Last.fm]: Found {len(tracks)} tracks via track.getSimilar for '{song_title}' by '{artist_name}'")

        # Strategy 2: If artist is known and track has no scrobbles, query the artist's similarity graph!
        # This keeps recommendations 100% in the artist's genre and scene instead of random fuzzy track matching
        if not tracks and artist_name:
            print(f"  └─ 🔍 [Last.fm]: Track-level similarity yielded 0 tracks. Querying similar artists for '{artist_name}'...")
            tracks = self._get_similar_artists_tracks(artist_name)
            if tracks:
                print(f"  └─ ✅ [Last.fm]: Found {len(tracks)} tracks via similar artists graph for '{artist_name}'")

        # Strategy 3: Candidate artist/track pairs for multi-word queries without delimiters
        if not tracks and " by " not in query and " - " not in query:
            clean_q = re.sub(r'[\(\[\{].*?[\)\]\}]', '', query).strip()
            words = clean_q.split()
            pairs = []
            # Trailing artist candidates (Song Title Artist)
            for k in (2, 1, 3):
                if len(words) > k:
                    pairs.append((" ".join(words[-k:]), " ".join(words[:-k]), k))
            # Leading artist candidates (Artist Song Title)
            for k in (2, 1, 3):
                if len(words) > k:
                    pairs.append((" ".join(words[:k]), " ".join(words[k:]), k))

            # 3A. Try direct track similarity for candidate pairs
            for cand_art, cand_trk, k in pairs:
                res = self._call("track.getsimilar", {
                    "artist": cand_art,
                    "track": cand_trk,
                    "limit": 50,
                    "autocorrect": 1
                })
                cand_tracks = self._parse_similar_tracks(res, exclude_song=cand_trk)
                if cand_tracks:
                    tracks = cand_tracks
                    print(f"  └─ ✅ [Last.fm]: Resolved pair '{cand_trk}' by '{cand_art}' ({len(tracks)} tracks)")
                    break

            # 3B. If track similarity yielded 0, detect if one of the candidate artist splits is a verified artist
            if not tracks:
                best_art = None
                best_score = 0
                for cand_art, cand_trk, k in pairs:
                    # Ignore single common dictionary words as artist in multi-word queries
                    if k == 1 and len(words) >= 3 and cand_art.lower() in ("nothing", "still", "love", "one", "the", "you", "me", "all", "stands", "give"):
                        continue
                    info = self._call("artist.getinfo", {"artist": cand_art})
                    listeners = int(info.get("artist", {}).get("stats", {}).get("listeners", 0) or 0)
                    weight = 2.0 if k >= 2 else 1.0
                    score = listeners * weight
                    if score > best_score and listeners > 1000:
                        best_score = score
                        best_art = cand_art

                if best_art:
                    print(f"  └─ 🔍 [Last.fm]: Detected artist '{best_art}' from query. Querying similar artists graph...")
                    tracks = self._get_similar_artists_tracks(best_art)
                    if tracks:
                        print(f"  └─ ✅ [Last.fm]: Found {len(tracks)} tracks via similar artists graph for '{best_art}'")

        # Strategy 4: Track search (with STRICT relevance validation to prevent cross-genre hijacking)
        if not tracks:
            search_res = self._call("track.search", {"track": query, "limit": 10})
            matches = search_res.get("results", {}).get("trackmatches", {}).get("track", [])
            if isinstance(matches, dict):
                matches = [matches]
            matches.sort(key=lambda x: int(x.get("listeners", 0) or 0), reverse=True)

            for m in matches[:5]:
                cand_track = m.get("name", "")
                cand_artist = m.get("artist", "")
                if cand_track and cand_artist and self._is_relevant_match(query, artist_name, cand_artist, cand_track, expected_track=song_title):
                    res = self._call("track.getsimilar", {
                        "artist": cand_artist,
                        "track": cand_track,
                        "limit": 50,
                        "autocorrect": 1
                    })
                    cand_tracks = self._parse_similar_tracks(res, exclude_song=cand_track)
                    if cand_tracks:
                        tracks = cand_tracks
                        print(f"  └─ ✅ [Last.fm]: Resolved canonical '{cand_track}' by '{cand_artist}' ({len(tracks)} similar tracks)")
                        break

        # Strategy 5: Generic artist similarity fallback if query was an artist name
        if not tracks:
            target_artist = artist_name or (query.split(" by ")[-1].strip() if " by " in query else "")
            if target_artist and target_artist.lower() not in ("unknown artist", "unknown", "release", "various artists"):
                tracks = self._get_similar_artists_tracks(target_artist)
                if tracks:
                    print(f"  └─ ✅ [Last.fm]: Found {len(tracks)} tracks via similar artists for '{target_artist}'")

        if not tracks:
            print(f"  └─ ⚠️ [Last.fm]: No similar tracks found for '{query}'")

        return tracks

    def get_verified_genre_artists(self, genre: str | None) -> set[str]:
        """Returns the cached set of verified primary-genre artist names (lowercase) for a genre tag."""
        if not genre:
            return set()
        raw_tag = genre.lower().strip()
        canon_tag = _canonicalize_lastfm_tag(raw_tag)
        return set(_LASTFM_VERIFIED_ARTISTS_CACHE.get(raw_tag, set())) | set(
            _LASTFM_VERIFIED_ARTISTS_CACHE.get(canon_tag, set())
        )

    def get_charts(self, genre: str = None) -> list[Track]:
        key = self.api_key or os.getenv("LASTFM_API_KEY")
        if not key:
            return []

        tracks = []
        if genre and genre.lower() not in ("all", "general", "none"):
            from collections import Counter
            from src.skills.music.engine.formatting import is_recently_played, RECENTLY_PLAYED_TRACKS, _norm_track_token
            from src.skills.music.playback.youtube_music import is_garbage_music_title
            raw_tag = genre.lower().strip()
            tag = _canonicalize_lastfm_tag(raw_tag)
            print(f"  └─ 📻 [Last.fm]: Fetching top tracks & verified genre artists for tag '{tag}'...")

            f_tracks = submit_to_worker(self._call, "tag.gettoptracks", {"tag": tag, "limit": 50, "page": 1})
            f_artists = submit_to_worker(self._call, "tag.gettopartists", {"tag": tag, "limit": 100})
            res = f_tracks.result(timeout=15)
            art_res = f_artists.result(timeout=15)

            raw = res.get("tracks", {}).get("track", [])
            if isinstance(raw, dict):
                raw = [raw]

            # Pull a rotating deeper page (Page 2 or 3) when Page 1 has a full catalog so Shelf 3 (Deep Cuts) is stocked
            deep_raw = []
            if len(raw) >= 20:
                deep_page = random.choice((2, 3))
                deep_res = self._call("tag.gettoptracks", {"tag": tag, "limit": 50, "page": deep_page})
                deep_raw = deep_res.get("tracks", {}).get("track", [])
                if isinstance(deep_raw, dict):
                    deep_raw = [deep_raw]

            raw_artists = art_res.get("topartists", {}).get("artist", [])
            if isinstance(raw_artists, dict):
                raw_artists = [raw_artists]

            verified_genre_artists = set()
            for a in raw_artists:
                a_name = a.get("name", "") if isinstance(a, dict) else str(a)
                if a_name and a_name.strip():
                    verified_genre_artists.add(a_name.lower().strip())

            recent_artist_norms = {rec_a for _, rec_a in RECENTLY_PLAYED_TRACKS if rec_a}
            artist_seen = set()
            source_name = f"lastfm_{tag.replace(' ', '_')}"

            def _filter_tag_track_pool(track_list: list) -> list:
                if not track_list:
                    return []
                cleaned = [
                    t for t in track_list
                    if not is_garbage_music_title(
                        t.get("name", ""),
                        query="",
                        author=t.get("artist", {}).get("name", "") if isinstance(t.get("artist"), dict) else str(t.get("artist", ""))
                    )
                    and not _ALBUM_FILLER_RE.search(t.get("name", ""))
                ]
                if cleaned:
                    track_list = cleaned
                if verified_genre_artists:
                    ver = [
                        t for t in track_list
                        if (t.get("artist", {}).get("name", "") if isinstance(t.get("artist"), dict) else str(t.get("artist", ""))).lower().strip()
                        in verified_genre_artists
                    ]
                    if ver:
                        track_list = ver
                unplayed = [
                    t for t in track_list
                    if not is_recently_played(
                        t.get("name", ""),
                        t.get("artist", {}).get("name", "") if isinstance(t.get("artist"), dict) else str(t.get("artist", ""))
                    )
                ]
                return unplayed if unplayed else track_list

            # Step A: Candidate tracks from tag.gettoptracks cross-verified against tag.gettopartists (Shelf 1 Core)
            sampled_candidates = []
            active_raw = _filter_tag_track_pool(raw)
            if active_raw:
                diverse_raw = []
                for t in active_raw:
                    art = t.get("artist", {}).get("name", "") if isinstance(t.get("artist"), dict) else str(t.get("artist", ""))
                    clean_art = art.lower().strip()
                    if clean_art and clean_art not in artist_seen:
                        artist_seen.add(clean_art)
                        diverse_raw.append(t)
                sampled_candidates = random.sample(diverse_raw, min(8, len(diverse_raw)))

            # Step A2: Deep-cut candidates from rotating Page 2/3 (Shelf 3 Deep Cuts)
            sampled_deep_candidates = []
            active_deep = _filter_tag_track_pool(deep_raw)
            if active_deep:
                diverse_deep = []
                for t in active_deep:
                    art = t.get("artist", {}).get("name", "") if isinstance(t.get("artist"), dict) else str(t.get("artist", ""))
                    clean_art = art.lower().strip()
                    if clean_art and clean_art not in artist_seen:
                        artist_seen.add(clean_art)
                        diverse_deep.append(t)
                sampled_deep_candidates = random.sample(diverse_deep, min(5, len(diverse_deep)))

            # Step B: Candidate rotating artists from tag.gettopartists (Shelf 2 Solid Artist Cuts)
            selected_artists = []
            if raw_artists:
                step_a_artists = {
                    (t.get("artist", {}).get("name", "") if isinstance(t.get("artist"), dict) else str(t.get("artist", ""))).lower().strip()
                    for t in (sampled_candidates + sampled_deep_candidates)
                }
                fresh_cand_artists = []
                fallback_cand_artists = []
                for a in raw_artists[:48]:
                    a_name = a.get("name", "") if isinstance(a, dict) else str(a)
                    clean_a = a_name.lower().strip()
                    if a_name and clean_a not in step_a_artists:
                        if _norm_track_token(a_name) not in recent_artist_norms:
                            fresh_cand_artists.append(a_name)
                        else:
                            fallback_cand_artists.append(a_name)
                cand_pool = fresh_cand_artists if len(fresh_cand_artists) >= 8 else (fresh_cand_artists + fallback_cand_artists)
                selected_artists = random.sample(cand_pool, min(8, len(cand_pool)))

            # Dynamic scrobble-tag consensus verification across all candidate artists (Step A + Step B)
            all_cand_artist_names = list(dict.fromkeys(
                [
                    (t.get("artist", {}).get("name", "") if isinstance(t.get("artist"), dict) else str(t.get("artist", "")))
                    for t in (sampled_candidates + sampled_deep_candidates)
                ]
                + selected_artists
            ))

            def _fetch_high_tags(a_name: str) -> tuple[str, set[str]]:
                tag_res = self._call("artist.gettoptags", {"artist": a_name, "autocorrect": 1})
                t_list = tag_res.get("toptags", {}).get("tag", [])
                if isinstance(t_list, dict):
                    t_list = [t_list]
                if not t_list:
                    return a_name.lower().strip(), set()
                high = set()
                for tg in t_list[:8]:
                    t_name = (tg.get("name") or "").lower().strip()
                    try:
                        t_count = int(tg.get("count", 0))
                    except (TypeError, ValueError):
                        t_count = 0
                    if t_name and t_count >= 50:
                        high.add(t_name)
                return a_name.lower().strip(), high

            unverified_cand_artists = [
                a for a in all_cand_artist_names
                if a.lower().strip() not in verified_genre_artists
            ]
            artist_high_tags: dict[str, set[str]] = {}
            tag_counter = Counter()
            if unverified_cand_artists:
                futs = [submit_to_worker(_fetch_high_tags, a) for a in unverified_cand_artists]
                for fut in futs:
                    clean_a, high_set = fut.result(timeout=15)
                    artist_high_tags[clean_a] = high_set
                    for h in high_set:
                        tag_counter[h] += 1

            dominant_tag = tag_counter.most_common(1)[0][0] if tag_counter else tag

            def _is_verified_primary_artist(a_name: str) -> bool:
                clean_a = a_name.lower().strip()
                if clean_a in verified_genre_artists:
                    return True
                high_set = artist_high_tags.get(clean_a)
                if not high_set:
                    return True  # Graceful pass-through if unmocked or artist has no tags
                if tag in high_set or dominant_tag in high_set:
                    return True
                return any(tag in h or h in tag for h in high_set)

            # Cache verified primary-genre artists for cross-source gatekeeping in MusicAggregatorManager
            verified_primary_set = {
                a for a in verified_genre_artists if _is_verified_primary_artist(a)
            }
            if verified_primary_set:
                _LASTFM_VERIFIED_ARTISTS_CACHE[tag] = verified_primary_set
                _LASTFM_VERIFIED_ARTISTS_CACHE[raw_tag] = verified_primary_set

            # Finalize Step A tracks -> Shelf 1 (up to 6 verified primary-genre Core tracks)
            artist_seen = set()
            if sampled_candidates:
                primary_sampled = [
                    t for t in sampled_candidates
                    if _is_verified_primary_artist(
                        t.get("artist", {}).get("name", "") if isinstance(t.get("artist"), dict) else str(t.get("artist", ""))
                    )
                ]
                final_step_a = (primary_sampled if primary_sampled else sampled_candidates)[:6]
                for t in final_step_a:
                    title = t.get("name", "")
                    artist_obj = t.get("artist", {})
                    artist = artist_obj.get("name", "") if isinstance(artist_obj, dict) else str(artist_obj)
                    if title and artist:
                        artist_seen.add(artist.lower().strip())
                        tracks.append(Track(
                            title=title,
                            artist=artist,
                            genre=genre.title(),
                            source=source_name,
                            tier=1,
                            relevance_score=1.0,
                            verified_genre=True,
                        ))

            # Finalize Step B tracks -> Shelf 2 (up to 6 rotating primary-genre artists' sweet-spot cuts #3-#12)
            verified_step_b_artists = [
                a_name for a_name in selected_artists
                if a_name.lower().strip() not in artist_seen and _is_verified_primary_artist(a_name)
            ][:6]

            def _fetch_tag_artist_top(a_name: str):
                top_res = self._call("artist.gettoptracks", {"artist": a_name, "limit": 15, "autocorrect": 1})
                top_trks = top_res.get("toptracks", {}).get("track", [])
                if isinstance(top_trks, dict):
                    top_trks = [top_trks]
                if top_trks:
                    clean_trks = [
                        tk for tk in top_trks[:15]
                        if tk.get("name")
                        and not is_garbage_music_title(tk.get("name", ""), query="", author=a_name)
                        and not _ALBUM_FILLER_RE.search(tk.get("name", ""))
                    ]
                    active_trks = clean_trks if clean_trks else top_trks[:5]
                    # Prefer mid-catalog sweet-spot cuts (#3-#12) over #1 radio single when available
                    mid_catalog = active_trks[2:12] if len(active_trks) >= 4 else active_trks
                    unplayed_mid = [
                        tk for tk in mid_catalog
                        if tk.get("name") and not is_recently_played(tk.get("name", ""), a_name)
                    ]
                    if unplayed_mid:
                        pool_trks = unplayed_mid
                        assigned_tier = 2
                    else:
                        unplayed_all = [
                            tk for tk in active_trks
                            if tk.get("name") and not is_recently_played(tk.get("name", ""), a_name)
                        ]
                        pool_trks = unplayed_all if unplayed_all else active_trks
                        assigned_tier = 2
                    if not pool_trks:
                        return None
                    chosen_t = random.choice(pool_trks)
                    t_title = chosen_t.get("name", "")
                    if t_title and not is_garbage_music_title(t_title, query="", author=a_name):
                        return Track(
                            title=t_title,
                            artist=a_name,
                            genre=genre.title(),
                            source=source_name,
                            tier=assigned_tier,
                            relevance_score=0.9,
                            verified_genre=True,
                        )
                return None

            if verified_step_b_artists:
                for trk in map_in_workers(_fetch_tag_artist_top, verified_step_b_artists):
                    if trk:
                        artist_seen.add(trk.artist.lower().strip())
                        tracks.append(trk)

            # Finalize Step A2 tracks -> Shelf 3 (up to 4 verified Deep Cuts from Page 2/3)
            if sampled_deep_candidates:
                primary_deep = [
                    t for t in sampled_deep_candidates
                    if _is_verified_primary_artist(
                        t.get("artist", {}).get("name", "") if isinstance(t.get("artist"), dict) else str(t.get("artist", ""))
                    )
                ]
                for t in (primary_deep or sampled_deep_candidates)[:4]:
                    title = t.get("name", "")
                    artist_obj = t.get("artist", {})
                    artist = artist_obj.get("name", "") if isinstance(artist_obj, dict) else str(artist_obj)
                    if title and artist and artist.lower().strip() not in artist_seen:
                        artist_seen.add(artist.lower().strip())
                        tracks.append(Track(
                            title=title,
                            artist=artist,
                            genre=genre.title(),
                            source=source_name,
                            tier=3,
                            relevance_score=0.85,
                            verified_genre=True,
                        ))
        else:
            print("  └─ 📻 [Last.fm]: Fetching global top chart tracks...")
            res = self._call("chart.gettoptracks", {"limit": 50})
            raw = res.get("tracks", {}).get("track", [])
            if isinstance(raw, dict):
                raw = [raw]
            if raw:
                artist_seen = set()
                diverse_raw = []
                for t in raw:
                    art = t.get("artist", {}).get("name", "") if isinstance(t.get("artist"), dict) else str(t.get("artist", ""))
                    clean_art = art.lower().strip()
                    if clean_art and clean_art not in artist_seen:
                        artist_seen.add(clean_art)
                        diverse_raw.append(t)
                sampled = random.sample(diverse_raw, min(10, len(diverse_raw)))
                for idx, t in enumerate(sampled):
                    title = t.get("name", "")
                    artist_obj = t.get("artist", {})
                    artist = artist_obj.get("name", "") if isinstance(artist_obj, dict) else str(artist_obj)
                    if title and artist:
                        tracks.append(Track(
                            title=title,
                            artist=artist,
                            source="lastfm_global",
                            tier=1 if idx < 5 else 2,
                        ))

        return tracks

    def get_artist_top_tracks(self, artist_name: str, limit: int = 20) -> list[Track]:
        """Fetches top scrobble-ranked tracks for an artist via Last.fm (with artist.search fallback)."""
        clean_name = (artist_name or "").strip()
        if not clean_name:
            return []

        key = self.api_key or os.getenv("LASTFM_API_KEY")
        if not key:
            return []

        print(f"  └─ 🎤 [Last.fm Artist Search]: Querying '{clean_name}'...")
        res = self._call("artist.gettoptracks", {
            "artist": clean_name,
            "limit": limit,
            "autocorrect": 1
        })
        raw_tracks = res.get("toptracks", {}).get("track", [])
        if isinstance(raw_tracks, dict):
            raw_tracks = [raw_tracks]

        # Fallback: If direct artist lookup returned 0 tracks, search for canonical artist name
        if not raw_tracks:
            search_res = self._call("artist.search", {"artist": clean_name, "limit": 1})
            matches = search_res.get("results", {}).get("artistmatches", {}).get("artist", [])
            if isinstance(matches, dict):
                matches = [matches]
            if matches:
                canonical_artist = matches[0].get("name", "").strip()
                if canonical_artist and canonical_artist.lower() != clean_name.lower():
                    print(f"  └─ 🔍 [Last.fm Artist]: Resolved '{clean_name}' -> '{canonical_artist}'")
                    res = self._call("artist.gettoptracks", {
                        "artist": canonical_artist,
                        "limit": limit,
                        "autocorrect": 1
                    })
                    raw_tracks = res.get("toptracks", {}).get("track", [])
                    if isinstance(raw_tracks, dict):
                        raw_tracks = [raw_tracks]

        tracks = []
        for idx, t in enumerate(raw_tracks[:limit]):
            title = t.get("name", "").strip()
            if not title or _ALBUM_FILLER_RE.search(title):
                continue
            artist_obj = t.get("artist", {})
            resolved_artist = (
                artist_obj.get("name", "").strip()
                if isinstance(artist_obj, dict)
                else str(artist_obj or clean_name).strip()
            ) or clean_name
            tier = 1 if idx < 5 else (2 if idx < 13 else 3)
            tracks.append(Track(
                title=title,
                artist=resolved_artist,
                source="lastfm_artist",
                tier=tier,
                verified_genre=True,
            ))

        if tracks:
            print(f"  └─ ✅ [Last.fm Artist]: Found {len(tracks)} top tracks for '{tracks[0].artist}'")
        return tracks


