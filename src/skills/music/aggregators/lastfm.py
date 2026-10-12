import os
import random
import requests
from dotenv import load_dotenv
from src.skills.music.aggregators.base import BaseMusicAggregator
from src.skills.music.models import Track
from src.music_utils import parse_song_meta

load_dotenv()

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
        """Helper to invoke Last.fm REST API with JSON format and error handling."""
        key = self.api_key or os.getenv("LASTFM_API_KEY")
        if not key:
            return {}
        p = {
            "method": method,
            "api_key": key,
            "format": "json",
            **params
        }
        try:
            r = requests.get(self.BASE_URL, params=p, timeout=6, headers={"User-Agent": "DiscordMusicBot/1.0"})
            if r.status_code == 200:
                return r.json()
            else:
                print(f"  └─ ⚠️ [Last.fm]: API returned status {r.status_code} for method '{method}'")
        except Exception as e:
            print(f"  └─ ⚠️ [Last.fm Error]: {e}")
        return {}

    def _parse_similar_tracks(self, data: dict, exclude_song: str = "") -> list[Track]:
        """Extracts Track models from a Last.fm similartracks response."""
        tracks = []
        raw = data.get("similartracks", {}).get("track", [])
        if isinstance(raw, dict):
            raw = [raw]
        for t in raw:
            title = t.get("name", "")
            artist_obj = t.get("artist", {})
            artist = artist_obj.get("name", "") if isinstance(artist_obj, dict) else str(artist_obj)
            if not title or not artist:
                continue
            if exclude_song and exclude_song.lower() == title.lower():
                continue
            tracks.append(Track(
                title=title,
                artist=artist,
                source="lastfm_similar"
            ))
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
        if artist_name.lower() in ("unknown artist", "unknown"):
            artist_name = ""

        # Strategy 1: Direct track.getSimilar if both song and artist were cleanly parsed
        if song_title and artist_name:
            res = self._call("track.getsimilar", {
                "artist": artist_name,
                "track": song_title,
                "limit": 25,
                "autocorrect": 1
            })
            tracks = self._parse_similar_tracks(res, exclude_song=song_title)
            if tracks:
                print(f"  └─ ✅ [Last.fm]: Found {len(tracks)} tracks via track.getSimilar for '{song_title}' by '{artist_name}'")

        # Strategy 2: Track search to resolve exact canonical artist and track title
        if not tracks:
            search_res = self._call("track.search", {"track": query, "limit": 3})
            matches = search_res.get("results", {}).get("trackmatches", {}).get("track", [])
            if isinstance(matches, dict):
                matches = [matches]
            for m in matches:
                cand_track = m.get("name", "")
                cand_artist = m.get("artist", "")
                if cand_track and cand_artist:
                    res = self._call("track.getsimilar", {
                        "artist": cand_artist,
                        "track": cand_track,
                        "limit": 25,
                        "autocorrect": 1
                    })
                    tracks = self._parse_similar_tracks(res, exclude_song=cand_track)
                    if tracks:
                        print(f"  └─ ✅ [Last.fm]: Resolved canonical '{cand_track}' by '{cand_artist}' ({len(tracks)} similar tracks)")
                        break

        # Strategy 3: Artist similarity fallback (artist.getsimilar -> top tracks)
        if not tracks:
            target_artist = artist_name or (query.split(" by ")[-1].strip() if " by " in query else query)
            artist_res = self._call("artist.getsimilar", {
                "artist": target_artist,
                "limit": 8,
                "autocorrect": 1
            })
            similar_artists = artist_res.get("similarartists", {}).get("artist", [])
            if isinstance(similar_artists, dict):
                similar_artists = [similar_artists]

            for sa in similar_artists[:6]:
                rel_name = sa.get("name", "")
                if rel_name:
                    top_res = self._call("artist.gettoptracks", {
                        "artist": rel_name,
                        "limit": 2,
                        "autocorrect": 1
                    })
                    top_tracks = top_res.get("toptracks", {}).get("track", [])
                    if isinstance(top_tracks, dict):
                        top_tracks = [top_tracks]
                    for tt in top_tracks:
                        t_name = tt.get("name", "")
                        if t_name:
                            tracks.append(Track(
                                title=t_name,
                                artist=rel_name,
                                source="lastfm_similar"
                            ))
            if tracks:
                print(f"  └─ ✅ [Last.fm]: Found {len(tracks)} tracks via similar artists for '{target_artist}'")

        if not tracks:
            print(f"  └─ ⚠️ [Last.fm]: No similar tracks found for '{query}'")

        return tracks

    def get_charts(self, genre: str = None) -> list[Track]:
        key = self.api_key or os.getenv("LASTFM_API_KEY")
        if not key:
            return []

        tracks = []
        if genre and genre.lower() not in ("all", "general", "none"):
            tag = genre.lower().strip()
            print(f"  └─ 📻 [Last.fm]: Fetching top tracks for tag '{tag}'...")
            res = self._call("tag.gettoptracks", {"tag": tag, "limit": 30})
            raw = res.get("tracks", {}).get("track", [])
            if isinstance(raw, dict):
                raw = [raw]
            if raw:
                sampled = random.sample(raw, min(10, len(raw)))
                for t in sampled:
                    title = t.get("name", "")
                    artist_obj = t.get("artist", {})
                    artist = artist_obj.get("name", "") if isinstance(artist_obj, dict) else str(artist_obj)
                    if title and artist:
                        tracks.append(Track(
                            title=title,
                            artist=artist,
                            genre=genre.title(),
                            source=f"lastfm_{tag.replace(' ', '_')}"
                        ))
        else:
            print("  └─ 📻 [Last.fm]: Fetching global top chart tracks...")
            res = self._call("chart.gettoptracks", {"limit": 35})
            raw = res.get("tracks", {}).get("track", [])
            if isinstance(raw, dict):
                raw = [raw]
            if raw:
                sampled = random.sample(raw, min(10, len(raw)))
                for t in sampled:
                    title = t.get("name", "")
                    artist_obj = t.get("artist", {})
                    artist = artist_obj.get("name", "") if isinstance(artist_obj, dict) else str(artist_obj)
                    if title and artist:
                        tracks.append(Track(
                            title=title,
                            artist=artist,
                            source="lastfm_global"
                        ))

        return tracks
