import re
import time
import random
import urllib.parse
import requests
from requests.adapters import HTTPAdapter
from src.skills.music.aggregators.base import BaseMusicAggregator
from src.skills.music.models import Track
from src.utils.cache import BoundedCache
from src.utils.http import ThreadLocalRequestsSession
from src.utils.workers import map_in_workers

_HTTP_SESSION = ThreadLocalRequestsSession(pool_connections=10, pool_maxsize=20)

def _http_get(url: str, **kwargs):
    return _HTTP_SESSION.get(url, **kwargs)

# 15-minute TTL cache for raw upstream Deezer lists (playlist searches, 40-track playlist pools, chart pools)
# Random sampling still runs fresh on every call to preserve 100% track/artist diversity.
_DEEZER_RAW_CACHE: dict[str, tuple[float, list]] = BoundedCache(maxsize=256)
_DEEZER_TRACK_DETAIL_CACHE: dict[str, tuple[float, dict]] = BoundedCache(maxsize=512)
_DEEZER_CACHE_TTL = 900.0

def clear_deezer_cache() -> None:
    _DEEZER_RAW_CACHE.clear()
    _DEEZER_TRACK_DETAIL_CACHE.clear()

def _get_cached_deezer_data(cache_key: str, url: str) -> list:
    now = time.monotonic()
    if cache_key in _DEEZER_RAW_CACHE:
        ts, cached_list = _DEEZER_RAW_CACHE[cache_key]
        if now - ts < _DEEZER_CACHE_TTL:
            return cached_list
    r = _http_get(url, timeout=5)
    data = r.json().get("data", [])
    if isinstance(data, list) and data:
        _DEEZER_RAW_CACHE[cache_key] = (now, data)
    return data if isinstance(data, list) else []

class DeezerAggregator(BaseMusicAggregator):
    """Music recommendation provider leveraging Deezer's public catalog and artist graph."""

    def resolve_track_details(self, title: str, artist: str = "", deezer_id: int = None) -> dict | None:
        """
        Resolve a track's single-track Deezer metadata (/track/{id}), returning:
        {'id': int, 'title': str, 'artist': str, 'album': str, 'preview': str, 'bpm': float, 'gain': float}
        """
        now = time.monotonic()
        clean_title = (title or "").strip()
        clean_artist = (artist or "").strip()
        if clean_artist.lower() in ("unknown artist", "unknown"):
            clean_artist = ""

        cache_key = f"id:{deezer_id}" if deezer_id else f"q:{clean_title.lower()}|{clean_artist.lower()}"
        if cache_key in _DEEZER_TRACK_DETAIL_CACHE:
            ts, cached_detail = _DEEZER_TRACK_DETAIL_CACHE[cache_key]
            if now - ts < _DEEZER_CACHE_TTL:
                return dict(cached_detail)

        resolved_id = deezer_id
        fallback_preview = None
        fallback_title = clean_title
        fallback_artist = clean_artist
        fallback_album = None

        if not resolved_id:
            if not clean_title:
                return None
            q_str = f"{clean_title} {clean_artist}".strip()
            try:
                r = _http_get(
                    f"https://api.deezer.com/search?q={urllib.parse.quote(q_str)}&limit=5",
                    timeout=5,
                )
                items = (r.json() or {}).get("data") or []
                if not items:
                    return None

                def _tokens(s: str) -> set[str]:
                    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) > 1}

                req_title_toks = _tokens(clean_title)
                req_art_toks = _tokens(clean_artist)
                norm_req_art = re.sub(r"[^a-z0-9]+", "", clean_artist.lower())
                norm_req_title = re.sub(r"[^a-z0-9]+", "", clean_title.lower())

                matched_item = None
                for cand_item in items:
                    c_title = cand_item.get("title") or ""
                    c_artist = (cand_item.get("artist") or {}).get("name") or ""
                    norm_c_art = re.sub(r"[^a-z0-9]+", "", c_artist.lower())
                    norm_c_title = re.sub(r"[^a-z0-9]+", "", c_title.lower())

                    title_ok = (
                        (norm_req_title and (norm_req_title in norm_c_title or norm_c_title in norm_req_title))
                        or bool(req_title_toks and req_title_toks.intersection(_tokens(c_title)))
                    )
                    if not title_ok:
                        continue

                    if clean_artist:
                        artist_ok = (
                            (norm_req_art and (norm_req_art in norm_c_art or norm_c_art in norm_req_art))
                            or bool(req_art_toks and req_art_toks.intersection(_tokens(c_artist)))
                        )
                        if not artist_ok:
                            continue

                    matched_item = cand_item
                    break

                if not matched_item:
                    return None

                resolved_id = matched_item.get("id")
                fallback_preview = matched_item.get("preview")
                fallback_title = matched_item.get("title") or clean_title
                fallback_artist = (matched_item.get("artist") or {}).get("name") or clean_artist
                fallback_album = (matched_item.get("album") or {}).get("title")
            except Exception:
                return None

        if not resolved_id:
            return None

        detail = {
            "id": resolved_id,
            "title": fallback_title,
            "artist": fallback_artist,
            "album": fallback_album,
            "preview": fallback_preview,
            "bpm": 0.0,
            "gain": None,
        }

        try:
            r_trk = _http_get(f"https://api.deezer.com/track/{resolved_id}", timeout=5)
            trk_json = r_trk.json() or {}
            if trk_json.get("id"):
                detail["title"] = trk_json.get("title") or detail["title"]
                detail["artist"] = (trk_json.get("artist") or {}).get("name") or detail["artist"]
                detail["album"] = (trk_json.get("album") or {}).get("title") or detail["album"]
                detail["preview"] = trk_json.get("preview") or detail["preview"]
                raw_bpm = trk_json.get("bpm")
                if raw_bpm is not None:
                    try:
                        detail["bpm"] = float(raw_bpm)
                    except (ValueError, TypeError):
                        pass
                raw_gain = trk_json.get("gain")
                if raw_gain is not None:
                    try:
                        detail["gain"] = float(raw_gain)
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass

        _DEEZER_TRACK_DETAIL_CACHE[cache_key] = (now, dict(detail))
        _DEEZER_TRACK_DETAIL_CACHE[f"id:{resolved_id}"] = (now, dict(detail))
        return detail

    def get_similar(self, query: str) -> list[Track]:
        print(f"  └─ 🔍 [Deezer]: Finding similar songs for '{query}'...")
        clean_q = re.sub(r'[\(\[\{].*?[\)\]\}]', '', query, flags=re.IGNORECASE)
        clean_q = re.sub(r'[-–—|/].*$', '', clean_q).strip()

        candidates = [query.strip()]
        if clean_q and clean_q != query:
            candidates.append(clean_q)
        if " by " in clean_q:
            parts = clean_q.split(" by ", 1)
            candidates.append(f"{parts[0].strip()} {parts[1].strip()}")
            candidates.append(parts[0].strip())
            candidates.append(parts[1].strip())
        elif " - " in clean_q:
            parts = clean_q.split(" - ", 1)
            candidates.append(f"{parts[1].strip()} {parts[0].strip()}")
            candidates.append(parts[1].strip())
            candidates.append(parts[0].strip())

        data = None
        for q_cand in candidates:
            if not q_cand.strip():
                continue
            try:
                r = _http_get(f"https://api.deezer.com/search?q={urllib.parse.quote(q_cand.strip())}&limit=1", timeout=5)
                res = r.json()
                if res.get("data"):
                    data = res
                    break
            except Exception:
                continue

        if not data or not data.get("data"):
            print("  └─ ⚠️ [Deezer]: Track not found in catalog.")
            return []

        track = data["data"][0]
        artist_id = track["artist"]["id"]
        track_title = track["title"]
        artist_name = track["artist"]["name"]
        print(f"  └─ ✅ [Deezer]: Resolved '{track_title}' by '{artist_name}'")

        similar_tracks = []
        try:
            r_rel = _http_get(f"https://api.deezer.com/artist/{artist_id}/related?limit=20", timeout=5)
            related_artists = r_rel.json().get("data", [])
            if related_artists:
                if len(related_artists) > 8:
                    related_artists = random.sample(related_artists, 8)

                def _fetch_rel_artist_top(rel_artist):
                    rel_tracks = []
                    try:
                        rel_id = rel_artist["id"]
                        top_data = _get_cached_deezer_data(
                            f"artist_top:{rel_id}:5",
                            f"https://api.deezer.com/artist/{rel_id}/top?limit=5"
                        )
                        for t in top_data:
                            if t.get('title', '').lower() != track_title.lower():
                                alb = t.get('album', {}).get('title')
                                rel_tracks.append(Track(
                                    title=t['title'],
                                    artist=t['artist']['name'],
                                    album=alb,
                                    source="deezer",
                                    deezer_id=t.get("id"),
                                    preview_url=t.get("preview"),
                                ))
                    except Exception:
                        pass
                    return rel_tracks

                for rel_list in map_in_workers(_fetch_rel_artist_top, related_artists):
                    similar_tracks.extend(rel_list)
            else:
                r_top = _http_get(f"https://api.deezer.com/artist/{artist_id}/top?limit=10", timeout=5)
                top = r_top.json()
                for t in top.get("data", []):
                    if t['title'].lower() != track_title.lower():
                        alb = t.get('album', {}).get('title')
                        similar_tracks.append(Track(
                            title=t['title'],
                            artist=t['artist']['name'],
                            album=alb,
                            source="deezer",
                            deezer_id=t.get("id"),
                            preview_url=t.get("preview"),
                        ))
        except Exception as e:
            print(f"  └─ ⚠️ [Deezer]: Error fetching recommendations: {e}")

        return similar_tracks

    def get_charts(self, genre: str = None) -> list[Track]:
        """Fetch curated playlist tracks for a query/vibe or global chart tracks from Deezer."""
        clean_g = (genre or "").lower().strip()
        tracks = []

        # Strategy 1: For specific playlist/vibe/Beatport queries, pool across up to 4 curated playlists
        if clean_g and clean_g not in ("all", "global", "general", "none"):
            try:
                print(f"  └─ 🎧 [Deezer]: Searching curated playlists for '{clean_g}'...")
                playlists = _get_cached_deezer_data(
                    f"pl_search:{clean_g}",
                    f"https://api.deezer.com/search/playlist?q={urllib.parse.quote(clean_g)}&limit=10"
                )
                if playlists:
                    valid_pls = [p for p in playlists if p.get("id") and p.get("nb_tracks", 0) >= 15]
                    if not valid_pls:
                        valid_pls = [p for p in playlists if p.get("id")]
                    valid_pls = valid_pls[:4]

                    selected_pls = random.sample(valid_pls, min(3, len(valid_pls)))
                    pooled_tracks = []
                    pl_titles = []

                    def _fetch_pl_tracks(pl):
                        pl_id = pl.get("id")
                        try:
                            data = _get_cached_deezer_data(
                                f"pl_tracks:{pl_id}",
                                f"https://api.deezer.com/playlist/{pl_id}/tracks?limit=40"
                            )
                            if data:
                                sampled_data = random.sample(data, min(8, len(data)))
                                local_tracks = []
                                for t in sampled_data:
                                    title = t.get("title", "Unknown")
                                    artist = t.get("artist", {}).get("name", "Unknown")
                                    if title and artist:
                                        local_tracks.append(Track(
                                            title=title,
                                            artist=artist,
                                            album=t.get("album", {}).get("title"),
                                            genre=clean_g.title(),
                                            source=f"deezer_{clean_g.replace(' ', '_')}",
                                            deezer_id=t.get("id"),
                                            preview_url=t.get("preview"),
                                        ))
                                return pl.get("title", str(pl_id)), local_tracks
                        except Exception:
                            pass
                        return None, []

                    if len(selected_pls) == 1:
                        t_name, t_list = _fetch_pl_tracks(selected_pls[0])
                        if t_name:
                            pl_titles.append(t_name)
                        pooled_tracks.extend(t_list)
                    elif selected_pls:
                        for t_name, t_list in map_in_workers(_fetch_pl_tracks, selected_pls):
                            if t_name:
                                pl_titles.append(t_name)
                            pooled_tracks.extend(t_list)

                    if pooled_tracks:
                        random.shuffle(pooled_tracks)
                        seen_keys = set()
                        for pt in pooled_tracks:
                            key = (pt.title.lower().strip(), pt.artist.lower().strip())
                            if key not in seen_keys:
                                seen_keys.add(key)
                                tracks.append(pt)
                        print(f"  └─ ✅ [Deezer]: Pooled {len(tracks)} diverse tracks across {len(pl_titles)} playlists ({', '.join(pl_titles[:3])}) for '{clean_g}'")
                        return tracks[:25]
            except Exception as e:
                print(f"  └─ ⚠️ [Deezer Playlist Search Warning]: {e}")
            return []

        # Strategy 2: Official global chart endpoint when no specific query is provided
        endpoint = "https://api.deezer.com/chart/0/tracks?limit=25"
        try:
            print("  └─ 🎧 [Deezer Charts]: Fetching Global charts...")
            data = _get_cached_deezer_data("chart:0", endpoint)
            for t in data:
                tracks.append(Track(
                    title=t.get("title", "Unknown"),
                    artist=t.get("artist", {}).get("name", "Unknown"),
                    album=t.get("album", {}).get("title"),
                    genre="Pop",
                    source="deezer_charts",
                    deezer_id=t.get("id"),
                    preview_url=t.get("preview"),
                ))
            return tracks
        except Exception as e:
            print(f"  └─ ⚠️ [Deezer Charts]: Error fetching charts: {e}")
            return []

    def get_artist_top_tracks(self, artist_name: str, limit: int = 15) -> list[Track]:
        """Search for an artist and retrieve their most popular top tracks."""
        clean_name = (artist_name or "").strip()
        if not clean_name:
            return []

        print(f"  └─ 🎤 [Deezer Artist Search]: Querying '{clean_name}'...")
        try:
            r = _http_get(f"https://api.deezer.com/search/artist?q={urllib.parse.quote(clean_name)}&limit=1", timeout=5)
            data = r.json().get("data", [])
            if not data:
                print(f"  └─ ⚠️ [Deezer Artist]: No artist found matching '{clean_name}'.")
                return []

            artist = data[0]
            artist_id = artist.get("id")
            resolved_artist_name = artist.get("name", clean_name)
            print(f"  └─ ✅ [Deezer Artist]: Found '{resolved_artist_name}' (ID: {artist_id})")

            r_top = _http_get(f"https://api.deezer.com/artist/{artist_id}/top?limit={limit}", timeout=5)
            top_tracks = r_top.json().get("data", [])

            tracks = []
            for t in top_tracks:
                tracks.append(Track(
                    title=t.get("title", "Unknown"),
                    artist=resolved_artist_name,
                    album=t.get("album", {}).get("title"),
                    source="deezer_artist",
                    deezer_id=t.get("id"),
                    preview_url=t.get("preview"),
                ))
            return tracks
        except Exception as e:
            print(f"  └─ ⚠️ [Deezer Artist]: Error fetching tracks for '{clean_name}': {e}")
            return []

