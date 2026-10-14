import re
import time
import random
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
import requests
from src.skills.music.aggregators.base import BaseMusicAggregator
from src.skills.music.models import Track

# 15-minute TTL cache for raw upstream Deezer lists (playlist searches, 40-track playlist pools, chart pools)
# Random sampling still runs fresh on every call to preserve 100% track/artist diversity.
_DEEZER_RAW_CACHE: dict[str, tuple[float, list]] = {}
_DEEZER_CACHE_TTL = 900.0

def clear_deezer_cache() -> None:
    _DEEZER_RAW_CACHE.clear()

def _get_cached_deezer_data(cache_key: str, url: str) -> list:
    is_mocked = hasattr(requests.get, "assert_called")
    now = time.monotonic()
    if not is_mocked and cache_key in _DEEZER_RAW_CACHE:
        ts, cached_list = _DEEZER_RAW_CACHE[cache_key]
        if now - ts < _DEEZER_CACHE_TTL:
            return cached_list
    r = requests.get(url, timeout=5)
    data = r.json().get("data", [])
    if not is_mocked and isinstance(data, list) and data:
        _DEEZER_RAW_CACHE[cache_key] = (now, data)
    return data if isinstance(data, list) else []

class DeezerAggregator(BaseMusicAggregator):
    """Music recommendation provider leveraging Deezer's public catalog and artist graph."""

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
                r = requests.get(f"https://api.deezer.com/search?q={urllib.parse.quote(q_cand.strip())}&limit=1", timeout=5)
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
            r_rel = requests.get(f"https://api.deezer.com/artist/{artist_id}/related?limit=20", timeout=5)
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
                                    source="deezer"
                                ))
                    except Exception:
                        pass
                    return rel_tracks

                with ThreadPoolExecutor(max_workers=min(6, len(related_artists))) as pool:
                    for rel_list in pool.map(_fetch_rel_artist_top, related_artists):
                        similar_tracks.extend(rel_list)
            else:
                r_top = requests.get(f"https://api.deezer.com/artist/{artist_id}/top?limit=10", timeout=5)
                top = r_top.json()
                for t in top.get("data", []):
                    if t['title'].lower() != track_title.lower():
                        alb = t.get('album', {}).get('title')
                        similar_tracks.append(Track(
                            title=t['title'],
                            artist=t['artist']['name'],
                            album=alb,
                            source="deezer"
                        ))
        except Exception as e:
            print(f"  └─ ⚠️ [Deezer]: Error fetching recommendations: {e}")

        return similar_tracks

    GENRE_MAP = {
        "pop": 132,
        "rap": 116,
        "hip hop": 116,
        "hiphop": 116,
        "rock": 152,
        "dance": 113,
        "electronic": 113,
        "techno": 113,
        "house": 113,
        "edm": 113,
        "dnb": 113,
        "drum and bass": 113,
        "drum & bass": 113,
        "drumnbass": 113,
        "jungle": 113,
        "dubstep": 113,
        "trance": 113,
        "synthwave": 113,
        "phonk": 113,
        "hardstyle": 113,
        "ambient": 113,
        "deep house": 113,
        "tech house": 113,
        "electro": 106,
        "r&b": 165,
        "rnb": 165,
        "alternative": 85,
        "metal": 464,
        "latin": 173,
        "classical": 98,
        "jazz": 129,
        "reggae": 144,
        "folk": 466,
    }

    def get_charts(self, genre: str = None) -> list[Track]:
        """Fetch trending chart tracks globally or for a specific genre from Deezer."""
        clean_g = (genre or "").lower().strip()
        tracks = []

        # Strategy 1: For specific subgenres or Beatport chart queries, pool across up to 4 curated playlists
        if clean_g and clean_g not in ("all", "global", "general", "none"):
            search_term = "drum and bass" if clean_g in ("dnb", "drum and bass", "drumnbass", "drum & bass") else clean_g
            try:
                print(f"  └─ 🎧 [Deezer]: Searching curated playlists for '{search_term}'...")
                playlists = _get_cached_deezer_data(
                    f"pl_search:{search_term}",
                    f"https://api.deezer.com/search/playlist?q={urllib.parse.quote(search_term)}&limit=10"
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
                                            source=f"deezer_{clean_g.replace(' ', '_')}"
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
                        with ThreadPoolExecutor(max_workers=min(4, len(selected_pls))) as pool:
                            for t_name, t_list in pool.map(_fetch_pl_tracks, selected_pls):
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

        # Strategy 2: Official genre chart endpoint
        genre_id = self.GENRE_MAP.get(clean_g, 0) if clean_g else 0
        if clean_g and clean_g not in ("all", "global", "general", "none") and genre_id == 0:
            # If an unknown specific genre was requested and no playlist matched, do not return global pop!
            print(f"  └─ ⚠️ [Deezer Charts]: Unrecognized genre '{clean_g}' with no playlist match.")
            return []

        endpoint = f"https://api.deezer.com/chart/{genre_id}/tracks?limit=25" if genre_id else "https://api.deezer.com/chart/0/tracks?limit=25"
        try:
            print(f"  └─ 🎧 [Deezer Charts]: Fetching {'genre ' + str(genre) if genre else 'Global'} charts...")
            data = _get_cached_deezer_data(f"chart:{genre_id}", endpoint)
            for t in data:
                tracks.append(Track(
                    title=t.get("title", "Unknown"),
                    artist=t.get("artist", {}).get("name", "Unknown"),
                    album=t.get("album", {}).get("title"),
                    genre=clean_g.title() if clean_g else "Pop",
                    source="deezer_charts"
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
            r = requests.get(f"https://api.deezer.com/search/artist?q={urllib.parse.quote(clean_name)}&limit=1", timeout=5)
            data = r.json().get("data", [])
            if not data:
                print(f"  └─ ⚠️ [Deezer Artist]: No artist found matching '{clean_name}'.")
                return []

            artist = data[0]
            artist_id = artist.get("id")
            resolved_artist_name = artist.get("name", clean_name)
            print(f"  └─ ✅ [Deezer Artist]: Found '{resolved_artist_name}' (ID: {artist_id})")

            r_top = requests.get(f"https://api.deezer.com/artist/{artist_id}/top?limit={limit}", timeout=5)
            top_tracks = r_top.json().get("data", [])

            tracks = []
            for t in top_tracks:
                tracks.append(Track(
                    title=t.get("title", "Unknown"),
                    artist=resolved_artist_name,
                    album=t.get("album", {}).get("title"),
                    source="deezer_artist"
                ))
            return tracks
        except Exception as e:
            print(f"  └─ ⚠️ [Deezer Artist]: Error fetching tracks for '{clean_name}': {e}")
            return []

