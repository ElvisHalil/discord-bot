"""
YouTube Music Native Radio & Studio Track Aggregator (`src/skills/music/aggregators/ytmusic.py`).

Uses `ytmusicapi` (unauthenticated `WEB_REMIX` InnerTube client) to tap into:
1. YouTube Music's native "Start Radio / Up Next" co-listening algorithm (`get_watch_playlist(radio=True)`).
2. Official `MUSIC_VIDEO_TYPE_ATV` (Audio Track Video) studio song resolution (`search(filter="songs")`),
   returning true `https://music.youtube.com/watch?v=<videoId>` links and exact track durations (`MM:SS`).
"""

import logging
import random
import re
import threading
import time
from typing import Dict, List, Optional, Tuple

from src.skills.music.aggregators.base import BaseMusicAggregator
from src.skills.music.engine.formatting import (
    MUSIC_METADATA_CACHE,
    _norm_track_token,
    is_recently_played,
    parse_song_meta,
)
from src.skills.music.models import Track
from src.utils.cache import BoundedCache

logger = logging.getLogger(__name__)


# ==============================================================================
# YOUTUBE MUSIC RADIO POOL SIZE SETTINGS (Tuneable Blend Ratios)
# ==============================================================================
# Context on Total Pool Sizes & How These Numbers Affect Track Selection:
#
# 1. `YTMUSIC_GENRE_RADIO_LIMIT = 15` (out of ~30 to 33 total tracks -> ~50% share)
#    - On genre, mood, and Autoplay requests (e.g. "acid techno", "driving techno",
#      "liquid dnb"), our scene aggregators (Last.fm + Beatport + Deezer) return
#      ~15 to 18 verified tracks.
#    - Setting this to `15` adds up to 15 YouTube Music Radio tracks (seeded from
#      verified scene artists in that subgenre), creating a ~30-33 track combined
#      pool (~50% YouTube Music Radio / ~50% Last.fm + Beatport + Deezer).
#    - Any track that appears in BOTH pools is automatically boosted to Shelf 1 (Tier 1).
#
# 2. `YTMUSIC_SIMILAR_RADIO_LIMIT = 18` (out of ~30 total tracks -> ~60% share)
#    - On "similar to" requests and 👍 song-card reactions, Last.fm + Deezer
#      typically return ~10 to 14 similar tracks (or fewer on underground tracks).
#    - Setting this to `18` adds up to 18 tracks from YouTube Music's single-song
#      Radio (`get_watch_playlist(videoId, radio=True)`), creating a ~30 track
#      combined pool (~60% YouTube Music Radio / ~40% Last.fm + Deezer).
#
# 3. `YTMUSIC_ARTIST_TRACK_LIMIT = 12` (out of ~25 total tracks -> ~50% share)
#    - On artist discovery requests, Last.fm + Deezer return ~12 to 15 tracks.
#    - Setting this to `12` adds up to 12 official YouTube Music studio (`ATV`)
#      tracks for a ~25 track combined artist pool (~50% share).
# ==============================================================================
YTMUSIC_GENRE_RADIO_LIMIT: int = 15      # 15 of ~30-33 total genre/autoplay tracks (~50% share)
YTMUSIC_SIMILAR_RADIO_LIMIT: int = 18    # 18 of ~30 total similarity/👍 tracks (~60% share)
YTMUSIC_ARTIST_TRACK_LIMIT: int = 12     # 12 of ~25 total artist tracks (~50% share)


_YTMUSIC_CLIENT = None
_YTMUSIC_LOCK = threading.Lock()

# 20-minute caches to prevent duplicate InnerTube requests and protect against rate limiting
_YTMUSIC_SEARCH_CACHE: Dict[str, Tuple[float, List[dict]]] = BoundedCache(maxsize=256)
_YTMUSIC_RADIO_CACHE: Dict[str, Tuple[float, List[dict]]] = BoundedCache(maxsize=128)
_YTMUSIC_CACHE_TTL: float = 1200.0


def clear_ytmusic_cache() -> None:
    """Clear in-memory `ytmusicapi` search and radio caches."""
    _YTMUSIC_SEARCH_CACHE.clear()
    _YTMUSIC_RADIO_CACHE.clear()


def _get_ytmusic_client():
    """Return a shared, lazily initialized `YTMusic` client instance."""
    global _YTMUSIC_CLIENT
    if _YTMUSIC_CLIENT is not None:
        return _YTMUSIC_CLIENT
    with _YTMUSIC_LOCK:
        if _YTMUSIC_CLIENT is None:
            try:
                from ytmusicapi import YTMusic
                _YTMUSIC_CLIENT = YTMusic()
            except Exception as e:
                logger.warning("Failed to initialize YTMusic client: %s", e)
                return None
    return _YTMUSIC_CLIENT


def parse_ytmusic_duration_seconds(raw_item: dict) -> Optional[int]:
    """Extract track duration in seconds from a `ytmusicapi` search or watch-playlist item."""
    if not isinstance(raw_item, dict):
        return None
    dur_sec = raw_item.get("duration_seconds")
    if isinstance(dur_sec, (int, float)) and dur_sec > 0:
        return int(dur_sec)
    raw_str = str(raw_item.get("duration") or raw_item.get("length") or "").strip()
    if not raw_str:
        return None
    parts = raw_str.split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except (TypeError, ValueError):
        pass
    return None


def _extract_clean_title_and_artist(item: dict) -> Tuple[str, str, Optional[str], str]:
    """
    Extract `(title, artist, album, video_type)` from a `ytmusicapi` item.
    For `UGC`/`OMV` uploads whose title is formatted as 'Artist - Title' or 'Artist -- Title',
    splits out the real artist and clean track title.
    """
    raw_title = str(item.get("title") or "").strip()
    artists_list = item.get("artists") or []
    raw_artist = ""
    if isinstance(artists_list, list) and artists_list:
        first_a = artists_list[0]
        raw_artist = str(first_a.get("name") if isinstance(first_a, dict) else first_a or "").strip()

    album_obj = item.get("album")
    album_name = str(album_obj.get("name") if isinstance(album_obj, dict) else album_obj or "").strip() or None
    video_type = str(item.get("videoType") or "").strip()

    # Clean label suffixes like "[Drumcode]" or "[Official Videoclip]" on non-ATV items
    title = re.sub(r"\s*\[(?:[^\]]*(?:official|video|videoclip|audio|premiere|out\s+now|free\s+download)[^\]]*)\]", "", raw_title, flags=re.IGNORECASE).strip()
    artist = raw_artist

    if video_type != "MUSIC_VIDEO_TYPE_ATV" and (" -- " in title or " - " in title):
        sep = " -- " if " -- " in title else " - "
        left, right = title.split(sep, 1)
        left, right = left.strip(), right.strip()
        if left and right:
            # If the uploader channel is a curator (e.g. 'ballacid', 'Techno District') or matches `left`, use `left` as artist
            artist = left
            title = re.sub(r"\s*\[[^\]]+\]\s*$", "", right).strip() or right

    return title, artist, album_name, video_type


def _cache_ytmusic_metadata(
    vid: str,
    title: str,
    artist: str,
    album: Optional[str] = None,
    genre: Optional[str] = None,
    duration_sec: Optional[int] = None,
) -> None:
    """Pre-populate `MUSIC_METADATA_CACHE[vid]` with official YTMusic metadata and duration."""
    if not vid or not title or not artist:
        return
    existing = MUSIC_METADATA_CACHE.get(vid) or {}
    eff_dur = duration_sec or existing.get("duration")
    MUSIC_METADATA_CACHE[vid] = {
        "song": title,
        "artist": artist,
        "album": album or existing.get("album"),
        "genre": genre or existing.get("genre"),
        "duration": eff_dur,
        "url": f"https://music.youtube.com/watch?v={vid}",
        "embeddable": existing.get("embeddable", True),
        "is_official_fallback": existing.get("is_official_fallback", False),
    }
    if eff_dur:
        try:
            from src.skills.youtube.engine.client import cache_video_duration

            cache_video_duration(vid, eff_dur)
        except Exception:
            pass


def search_ytmusic_songs(query: str, limit: int = 5) -> List[dict]:
    """
    Search YouTube Music's official `songs` (`MUSIC_VIDEO_TYPE_ATV`) catalog.
    Returns raw `ytmusicapi` song dicts and pre-caches metadata/durations.
    """
    clean_q = (query or "").strip()
    if not clean_q:
        return []

    cache_key = f"{clean_q.lower()}:{limit}"
    now = time.monotonic()
    if cache_key in _YTMUSIC_SEARCH_CACHE:
        ts, cached = _YTMUSIC_SEARCH_CACHE[cache_key]
        if now - ts < _YTMUSIC_CACHE_TTL:
            return cached

    yt = _get_ytmusic_client()
    if yt is None:
        return []

    try:
        search_q = re.sub(r"\s+by\s+", " ", clean_q, flags=re.IGNORECASE).strip()
        results = yt.search(search_q, filter="songs", limit=max(limit, 5)) or []
        valid_results: List[dict] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            vid = str(item.get("videoId") or "").strip()
            if not vid:
                continue
            title, artist, album, _ = _extract_clean_title_and_artist(item)
            dur_sec = parse_ytmusic_duration_seconds(item)
            if title and artist:
                _cache_ytmusic_metadata(vid, title, artist, album=album, duration_sec=dur_sec)
                valid_results.append(item)
        _YTMUSIC_SEARCH_CACHE[cache_key] = (now, valid_results)
        return valid_results[:limit]
    except Exception as e:
        logger.debug("YTMusic song search failed for %r: %s", clean_q, e)
        return []


def fetch_ytmusic_watch_radio(
    video_id: Optional[str] = None,
    playlist_id: Optional[str] = None,
    limit: int = 25,
) -> List[dict]:
    """
    Fetch YouTube Music's native 'Up Next / Start Radio' queue (`get_watch_playlist(radio=True)`)
    for a seed `video_id` or `playlist_id`.
    """
    if not video_id and not playlist_id:
        return []

    cache_key = f"vid:{video_id or ''}|pl:{playlist_id or ''}:{limit}"
    now = time.monotonic()
    if cache_key in _YTMUSIC_RADIO_CACHE:
        ts, cached = _YTMUSIC_RADIO_CACHE[cache_key]
        if now - ts < _YTMUSIC_CACHE_TTL:
            return cached

    yt = _get_ytmusic_client()
    if yt is None:
        return []

    try:
        kwargs = {"radio": True, "limit": max(limit, 20)}
        if video_id:
            kwargs["videoId"] = video_id
        if playlist_id:
            kwargs["playlistId"] = playlist_id.removeprefix("VL")
        wp = yt.get_watch_playlist(**kwargs) or {}
        raw_tracks = wp.get("tracks") or []
        valid: List[dict] = [t for t in raw_tracks if isinstance(t, dict) and t.get("videoId")]
        _YTMUSIC_RADIO_CACHE[cache_key] = (now, valid)
        return valid
    except Exception as e:
        logger.debug("YTMusic get_watch_playlist failed for (%r, %r): %s", video_id, playlist_id, e)
        return []


def _match_ytmusic_song_hit(
    hits: List[dict],
    target_song: str,
    target_artist: str = "",
) -> Optional[dict]:
    """
    Return the first `ytmusicapi` search hit whose song title (and artist, when provided)
    genuinely matches `target_song` and `target_artist`. Prevents starting a Radio queue
    from an unrelated fuzzy search hit when a track is completely unknown.
    """
    if not hits:
        return None
    s_norm = _norm_track_token(target_song)
    s_base = _norm_track_token(re.sub(r"[\(\[\{].*?[\)\]\}]", "", target_song or ""))
    a_raw = (target_artist or "").strip()
    a_norm = "" if a_raw.lower() in ("unknown artist", "unknown", "") else _norm_track_token(a_raw)

    if not s_norm:
        return hits[0]

    for item in hits:
        if not isinstance(item, dict) or not item.get("videoId"):
            continue
        it_title, it_artist, _, _ = _extract_clean_title_and_artist(item)
        it_s = _norm_track_token(it_title)
        it_s_base = _norm_track_token(re.sub(r"[\(\[\{].*?[\)\]\}]", "", it_title or ""))
        if not it_s:
            continue

        title_ok = (
            s_norm == it_s
            or (s_base and (s_base == it_s_base or s_base in it_s))
            or (it_s_base and len(it_s_base) >= 4 and it_s_base in s_norm)
        )
        if not title_ok:
            continue

        if a_norm:
            art_tokens = [_norm_track_token(it_artist)] + [
                _norm_track_token(str(a.get("name") if isinstance(a, dict) else a or ""))
                for a in (item.get("artists") or [])
            ]
            artist_ok = any(
                (a_norm in tok or tok in a_norm)
                for tok in art_tokens
                if tok
            ) or (a_norm in it_s)
            if not artist_ok:
                continue

        return item
    return None


class YouTubeMusicAggregator(BaseMusicAggregator):
    """
    Aggregator backed by `ytmusicapi` (`YTMusic`):
    - Generates single-track and genre-seeded Radio queues via `get_watch_playlist(radio=True)`.
    - Pre-attaches official `https://music.youtube.com/watch?v=<videoId>` studio (`ATV`) links
      and exact track durations in seconds.
    """

    def _build_tracks_from_radio_items(
        self,
        items: List[dict],
        max_tracks: int,
        exclude_query: Optional[str] = None,
        genre_label: Optional[str] = None,
        verified_genre: bool = False,
        max_per_artist: int = 2,
    ) -> List[Track]:
        from src.skills.music.playback.youtube_music import is_garbage_music_title

        seed_song_norm = ""
        seed_art_norm = ""
        if exclude_query:
            meta = parse_song_meta(exclude_query)
            seed_song_norm = _norm_track_token(meta.get("song", ""))
            seed_art_norm = _norm_track_token(meta.get("artist", ""))

        # Prefer pure studio `MUSIC_VIDEO_TYPE_ATV` tracks first unless caller already pre-ordered by genre verification
        if any(it.get("_genre_rank") is not None for it in items):
            ordered_items = sorted(
                items,
                key=lambda it: (
                    int(it.get("_genre_rank", 2)),
                    0 if str(it.get("videoType") or "") == "MUSIC_VIDEO_TYPE_ATV" else 1,
                ),
            )
        else:
            atv_items = [it for it in items if str(it.get("videoType") or "") == "MUSIC_VIDEO_TYPE_ATV"]
            other_items = [it for it in items if str(it.get("videoType") or "") != "MUSIC_VIDEO_TYPE_ATV"]
            ordered_items = atv_items + other_items

        tracks: List[Track] = []
        artist_counts: Dict[str, int] = {}
        seen_pairs: set[Tuple[str, str]] = set()

        t1_cutoff = max(1, int(round(max_tracks * 0.35)))
        t2_cutoff = max(t1_cutoff + 1, int(round(max_tracks * 0.75)))

        for item in ordered_items:
            if len(tracks) >= max_tracks:
                break
            vid = str(item.get("videoId") or "").strip()
            if not vid:
                continue
            title, artist, album, _ = _extract_clean_title_and_artist(item)
            if not title or not artist:
                continue
            if is_garbage_music_title(title, query="", author=artist):
                continue

            dur_sec = parse_ytmusic_duration_seconds(item)
            # Skip ultra-short intros/interludes (< 65s) or long continuous mixes (> 12m)
            if dur_sec is not None and (dur_sec < 65 or dur_sec > 720):
                continue

            s_norm = _norm_track_token(title)
            a_norm = _norm_track_token(artist)
            if not s_norm or not a_norm:
                continue

            if seed_song_norm and s_norm == seed_song_norm:
                if not seed_art_norm or seed_art_norm in a_norm or a_norm in seed_art_norm:
                    continue

            pair_key = (s_norm, a_norm)
            if pair_key in seen_pairs:
                continue
            if artist_counts.get(a_norm, 0) >= max_per_artist:
                continue

            seen_pairs.add(pair_key)
            artist_counts[a_norm] = artist_counts.get(a_norm, 0) + 1

            idx = len(tracks)
            if idx < t1_cutoff:
                tier = 1
                rel_score = 0.95
            elif idx < t2_cutoff:
                tier = 2
                rel_score = 0.84
            else:
                tier = 3
                rel_score = 0.72

            _cache_ytmusic_metadata(
                vid,
                title,
                artist,
                album=album,
                genre=genre_label,
                duration_sec=dur_sec,
            )
            tracks.append(
                Track(
                    title=title,
                    artist=artist,
                    album=album,
                    genre=genre_label,
                    url=f"https://music.youtube.com/watch?v={vid}",
                    source="ytmusic_radio",
                    tier=tier,
                    relevance_score=rel_score,
                    verified_genre=verified_genre,
                )
            )

        return tracks

    def get_similar(self, query: str) -> List[Track]:
        """
        Generate a YouTube Music Radio queue (`YTMUSIC_SIMILAR_RADIO_LIMIT` tracks)
        from a seed song query ('Song Title by Artist Name').
        """
        clean_q = (query or "").strip()
        if not clean_q:
            return []

        print(f"  └─ 📻 [YouTube Music Radio]: Fetching up to {YTMUSIC_SIMILAR_RADIO_LIMIT} co-listened tracks for '{clean_q}'...")
        meta = parse_song_meta(clean_q)
        song_hits = search_ytmusic_songs(clean_q, limit=4)
        matched_hit = _match_ytmusic_song_hit(
            song_hits,
            target_song=meta.get("song", clean_q),
            target_artist=meta.get("artist", ""),
        )
        if not matched_hit:
            return []

        seed_vid = str(matched_hit.get("videoId") or "").strip()
        if not seed_vid:
            return []

        radio_items = fetch_ytmusic_watch_radio(
            video_id=seed_vid,
            limit=YTMUSIC_SIMILAR_RADIO_LIMIT + 12,
        )
        if not radio_items:
            return []

        return self._build_tracks_from_radio_items(
            radio_items,
            max_tracks=YTMUSIC_SIMILAR_RADIO_LIMIT,
            exclude_query=clean_q,
            verified_genre=True,
            max_per_artist=2,
        )

    def get_charts(
        self,
        genre: str = None,
        seed_tracks: Optional[List[Track]] = None,
    ) -> List[Track]:
        """
        Fetch up to `YTMUSIC_GENRE_RADIO_LIMIT` tracks for `genre`:
        - Step A: If `seed_tracks` from Last.fm / Beatport are available, resolves 1-2 verified
          scene seed tracks to their official YTMusic `ATV` `videoId` and pulls their Radio queue
          so the tracks are 100% anchored to authentic artists in that subgenre.
        - Step B: Supplements or falls back via YouTube Music community playlists for `genre`.
        """
        clean_g = (genre or "").strip()
        display_genre = clean_g.title() if clean_g and clean_g.lower() not in ("all", "global", "general", "none") else None
        combined_radio_items: List[dict] = []

        # Step A: Anchor YTMusic Radio using 2-3 verified scene tracks (strictly preferring Last.fm canonical tracks over Deezer playlists)
        if seed_tracks:
            lfm_seeds = [
                t for t in seed_tracks
                if (t.source or "").startswith("lastfm_") and not is_recently_played(t.title, t.artist)
            ]
            verified_seeds = lfm_seeds or [
                t for t in seed_tracks
                if getattr(t, "verified_genre", False) and not is_recently_played(t.title, t.artist)
            ] or list(seed_tracks)
            tier1_seeds = [t for t in verified_seeds if getattr(t, "tier", 1) == 1] or verified_seeds
            chosen_seeds = random.sample(tier1_seeds, min(3, len(tier1_seeds)))
            seed_queues: List[List[dict]] = []
            for s_trk in chosen_seeds:
                hits = search_ytmusic_songs(f"{s_trk.title} {s_trk.artist}", limit=3)
                matched = _match_ytmusic_song_hit(hits, s_trk.title, s_trk.artist)
                if matched and matched.get("videoId"):
                    s_vid = str(matched["videoId"])
                    r_items = fetch_ytmusic_watch_radio(
                        video_id=s_vid,
                        limit=YTMUSIC_GENRE_RADIO_LIMIT + 8,
                    )
                    # Skip index 0 if it is the seed track itself (already in `seed_tracks`)
                    q_slice = r_items[1:] if len(r_items) > 1 else r_items
                    if q_slice:
                        seed_queues.append(q_slice)
            # Interleave round-robin across all resolved seed queues so one seed doesn't monopolize all 15 slots
            if seed_queues:
                max_len = max(len(q) for q in seed_queues)
                interleaved: List[dict] = []
                for i in range(max_len):
                    for q in seed_queues:
                        if i < len(q):
                            interleaved.append(q[i])

                # Cross-verify radio items against Last.fm's verified genre artist cache for the current genre tags
                # so YouTube Music Radio's off-genre drift (e.g. pop/breakcore mixed into a Liquid DnB radio queue)
                # doesn't pollute the pool, and prioritize verified artists not already in `seed_tracks` so
                # artist-diversity sampling keeps a true 50/50 split.
                from src.skills.music.aggregators.lastfm import _LASTFM_VERIFIED_ARTISTS_CACHE
                active_lfm_tags: set[str] = set()
                if clean_g:
                    active_lfm_tags.add(clean_g.lower())
                for t in seed_tracks:
                    src = (t.source or "").strip().lower()
                    if src.startswith("lastfm_"):
                        active_lfm_tags.add(src[len("lastfm_"):].replace("_", " "))

                verified_norms: set[str] = set()
                for tag_key in active_lfm_tags:
                    for a_name in _LASTFM_VERIFIED_ARTISTS_CACHE.get(tag_key, set()):
                        norm_a = _norm_track_token(a_name)
                        if norm_a:
                            verified_norms.add(norm_a)
                seed_artist_norms: set[str] = {
                    _norm_track_token(t.artist)
                    for t in seed_tracks
                    if getattr(t, "verified_genre", False) and t.artist
                }
                verified_norms.update(seed_artist_norms)

                if verified_norms:
                    for it in interleaved:
                        _, primary_art, _, _ = _extract_clean_title_and_artist(it)
                        art_norms = [
                            _norm_track_token(str(a.get("name") if isinstance(a, dict) else a or ""))
                            for a in (it.get("artists") or [])
                        ]
                        if primary_art:
                            art_norms.append(_norm_track_token(primary_art))
                        art_norms = [a for a in art_norms if a]
                        it_copy = dict(it)
                        if any(a in verified_norms for a in art_norms):
                            # Rank 0: Verified genre artist NOT already in `seed_tracks` (new artist for pool!)
                            # Rank 1: Verified genre artist already in `seed_tracks`
                            it_copy["_genre_rank"] = 1 if any(a in seed_artist_norms for a in art_norms) else 0
                        else:
                            # Rank 2: Unverified radio drift track (only used if Ranks 0+1 have < 15 tracks)
                            it_copy["_genre_rank"] = 2
                        combined_radio_items.append(it_copy)
                else:
                    combined_radio_items.extend(interleaved)

        # Step B: If no seed tracks were provided (or Step A yielded nothing), anchor via a YTMusic community playlist
        if not combined_radio_items and clean_g:
            yt = _get_ytmusic_client()
            if yt is not None:
                try:
                    pls = yt.search(clean_g, filter="community_playlists", limit=3) or []
                    for pl in pls[:2]:
                        b_id = str((pl or {}).get("browseId") or "").strip()
                        if not b_id:
                            continue
                        pl_items = fetch_ytmusic_watch_radio(
                            playlist_id=b_id,
                            limit=YTMUSIC_GENRE_RADIO_LIMIT + 6,
                        )
                        if pl_items:
                            # Upgrade the #1 playlist track to its studio ATV Radio queue if possible
                            t0_title, t0_art, _, _ = _extract_clean_title_and_artist(pl_items[0])
                            atv_hits = search_ytmusic_songs(f"{t0_title} {t0_art}", limit=1) if (t0_title and t0_art) else []
                            if atv_hits and atv_hits[0].get("videoId"):
                                combined_radio_items.extend(
                                    fetch_ytmusic_watch_radio(
                                        video_id=str(atv_hits[0]["videoId"]),
                                        limit=YTMUSIC_GENRE_RADIO_LIMIT + 8,
                                    )
                                )
                            combined_radio_items.extend(pl_items)
                            break
                except Exception as e:
                    logger.debug("YTMusic genre playlist radio fallback failed for %r: %s", clean_g, e)

        if not combined_radio_items:
            return []

        print(
            f"  └─ 📻 [YouTube Music Radio]: Adding up to {YTMUSIC_GENRE_RADIO_LIMIT} radio tracks "
            f"for '{display_genre or 'Top Charts'}'..."
        )
        return self._build_tracks_from_radio_items(
            combined_radio_items,
            max_tracks=YTMUSIC_GENRE_RADIO_LIMIT,
            genre_label=display_genre,
            verified_genre=True,
            max_per_artist=1,
        )

    def get_artist_top_tracks(self, artist: str) -> List[Track]:
        """Fetch up to `YTMUSIC_ARTIST_TRACK_LIMIT` official studio (`ATV`) tracks for `artist`."""
        clean_a = (artist or "").strip()
        if not clean_a:
            return []

        norm_target = _norm_track_token(clean_a)
        hits = search_ytmusic_songs(clean_a, limit=YTMUSIC_ARTIST_TRACK_LIMIT + 8)
        if not hits:
            return []

        tracks: List[Track] = []
        seen_titles: set[str] = set()
        for item in hits:
            if len(tracks) >= YTMUSIC_ARTIST_TRACK_LIMIT:
                break
            vid = str(item.get("videoId") or "").strip()
            title, art_name, album, _ = _extract_clean_title_and_artist(item)
            if not vid or not title or not art_name:
                continue
            art_tokens = [
                _norm_track_token(str(a.get("name") if isinstance(a, dict) else a or ""))
                for a in (item.get("artists") or [])
            ]
            target_words = re.sub(r"[^a-z0-9\s]", " ", clean_a.lower()).split()
            art_word_lists = [
                re.sub(r"[^a-z0-9\s]", " ", str(a.get("name") if isinstance(a, dict) else a or "").lower()).split()
                for a in (item.get("artists") or [])
            ]
            if norm_target and not any(
                (tok == norm_target or norm_target in tok.split()) and w_list == target_words
                for tok, w_list in zip(art_tokens, art_word_lists)
                if tok
            ):
                continue
            t_norm = _norm_track_token(title)
            if not t_norm or t_norm in seen_titles:
                continue
            seen_titles.add(t_norm)

            idx = len(tracks)
            tier = 1 if idx < 5 else (2 if idx < 9 else 3)
            tracks.append(
                Track(
                    title=title,
                    artist=art_name,
                    album=album,
                    url=f"https://music.youtube.com/watch?v={vid}",
                    source="ytmusic_artist",
                    tier=tier,
                    relevance_score=0.95 if tier == 1 else 0.85,
                    verified_genre=True,
                )
            )
        return tracks
