import os
import re
import json
import requests
from dotenv import load_dotenv
from src.skills.music.aggregators.base import BaseMusicAggregator
from src.skills.music.models import Track
from src.music_utils import parse_song_meta

load_dotenv()

CACHE_DIR = "data"
CACHE_PATH = os.path.join(CACHE_DIR, "freqblog_cache.json")


class FreqBlogAggregator(BaseMusicAggregator):
    """
    Music intelligence and acoustic recommendation provider leveraging FreqBlog API.
    Features:
    - PANNs neural audio embeddings and 18-feature acoustic similarity.
    - Deep audio features (BPM, key/Camelot, energy, mood, danceability).
    - DJ harmonic mixing, transitions, and continuous Camelot wheel playlists.
    - Strict local caching and circuit breaker to protect API quotas.
    """
    BASE_URL = "https://api.freqblog.com"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("FREQBLOG_API_KEY")
        self._circuit_broken = False
        self.cache = self._load_cache()

    def _load_cache(self) -> dict:
        """Loads cached song lookups to avoid redundant API calls."""
        if os.path.exists(CACHE_PATH):
            try:
                with open(CACHE_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"⚠️ [FreqBlog Cache]: Could not read cache file: {e}")
        return {}

    def _save_cache(self):
        """Persists cache to disk."""
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"⚠️ [FreqBlog Cache]: Failed to save cache file: {e}")

    def _call(self, method: str, endpoint: str, params: dict = None, json_body: dict = None) -> dict:
        """
        Invokes FreqBlog REST API with error handling, quota monitoring, and circuit breaker.
        """
        key = self.api_key or os.getenv("FREQBLOG_API_KEY")
        if not key:
            return {}

        if self._circuit_broken:
            return {}

        url = f"{self.BASE_URL}{endpoint}"
        headers = {
            "X-Api-Key": key,
            "User-Agent": "DiscordMusicBot/1.0",
        }

        try:
            if method.upper() == "POST":
                headers["Content-Type"] = "application/json"
                r = requests.post(url, headers=headers, json=json_body, params=params, timeout=6)
            else:
                r = requests.get(url, headers=headers, params=params, timeout=6)

            if r.status_code == 200:
                return r.json()
            elif r.status_code == 202:
                # On-demand ingest queued; not ready yet
                return {}
            elif r.status_code == 404:
                # Track not found / uncharged miss
                return {}
            elif r.status_code in (402, 429):
                # Monthly quota reached or rate limit exceeded: engage circuit breaker
                self._circuit_broken = True
                print(f"  └─ ⚠️ [FreqBlog Quota Warning]: Quota or rate limit reached (HTTP {r.status_code}). Engaging circuit breaker.")
                return {}
            else:
                print(f"  └─ ⚠️ [FreqBlog]: API returned status {r.status_code} for {endpoint}")
        except Exception as e:
            print(f"  └─ ⚠️ [FreqBlog Error]: {e}")

        return {}

    def lookup(self, track: str, artist: str = "") -> dict:
        """
        Looks up track audio features and metadata (BPM, key, camelot, energy, itunes_track_id).
        Checks local cache first to ensure 0 API calls for previously resolved tracks.
        """
        clean_track = re.sub(r'[\(\[\{].*?[\)\]\}]', '', track).strip()
        clean_artist = artist.strip()
        cache_key = f"{clean_track.lower()}|{clean_artist.lower()}"

        if cache_key in self.cache:
            return self.cache[cache_key]

        params = {"track": clean_track, "wait": 2}
        if clean_artist:
            params["artist"] = clean_artist

        data = self._call("GET", "/lookup", params=params)
        if data and isinstance(data, dict) and data.get("track_name"):
            res = {
                "track_name": data.get("track_name"),
                "artist_name": data.get("artist_name"),
                "bpm": data.get("bpm"),
                "key": data.get("key"),
                "camelot": data.get("camelot"),
                "open_key": data.get("open_key"),
                "energy": data.get("energy"),
                "mood": data.get("mood"),
                "genre": data.get("genre"),
                "itunes_track_id": data.get("itunes_track_id"),
                "album_name": data.get("album_name"),
                "release_date": data.get("release_date"),
            }
            self.cache[cache_key] = res
            self._save_cache()
            return res

        return {}

    def get_similar(self, query: str) -> list[Track]:
        """
        Tertiary fallback for song recommendations using PANNs acoustic neural embeddings.
        Only invoked when Last.fm and Deezer return 0 tracks.
        """
        key = self.api_key or os.getenv("FREQBLOG_API_KEY")
        if not key or self._circuit_broken:
            return []

        print(f"  └─ 🔍 [FreqBlog]: Finding acoustic similarity for '{query}'...")
        meta = parse_song_meta(query)
        song_title = meta.get("song", "").strip()
        artist_name = meta.get("artist", "").strip()
        if artist_name.lower() in ("unknown artist", "unknown"):
            artist_name = ""

        lookup_data = self.lookup(song_title, artist_name)
        track_id = lookup_data.get("itunes_track_id")

        if not track_id:
            print("  └─ ⚠️ [FreqBlog]: Track ID not available in catalog for acoustic similarity.")
            return []

        res = self._call("GET", "/similar", params={
            "track_id": track_id,
            "limit": 10,
            "exclude_same_artist": "true",
        })

        tracks = []
        raw_results = res.get("results", [])
        if isinstance(raw_results, list):
            for item in raw_results:
                t_obj = item.get("track", {})
                title = t_obj.get("track_name", "")
                artist = t_obj.get("artist_name", "")
                if title and artist:
                    tracks.append(Track(
                        title=title,
                        artist=artist,
                        source="freqblog_acoustic"
                    ))

        if tracks:
            print(f"  └─ ✅ [FreqBlog]: Found {len(tracks)} acoustically similar tracks via PANNs audio embeddings.")

        return tracks

    def get_charts(self, genre: str = None) -> list[Track]:
        """
        Fetches country top charts (Apple Music RSS linked to audio features).
        Default country is US unless specified.
        """
        key = self.api_key or os.getenv("FREQBLOG_API_KEY")
        if not key or self._circuit_broken:
            return []

        country = "us"
        if genre and len(genre.strip()) == 2:
            country = genre.strip().lower()

        res = self._call("GET", f"/charts/{country}")
        tracks = []
        chart_list = res.get("chart", [])
        if isinstance(chart_list, list):
            for item in chart_list[:10]:
                title = item.get("track_name", "")
                artist = item.get("artist_name", "")
                g = item.get("genre", "")
                if title and artist:
                    tracks.append(Track(
                        title=title,
                        artist=artist,
                        genre=g or None,
                        source=f"freqblog_chart_{country.upper()}"
                    ))
        return tracks

    # =========================================================================
    # Specialized Audio Intelligence (Called ONLY on explicit user prompts)
    # =========================================================================

    def get_audio_features(self, track: str, artist: str = "") -> dict:
        """Explicit audio feature lookup (BPM, key, energy, mood). Checks cache first."""
        return self.lookup(track, artist)

    def find_by_bpm(self, bpm: int, tolerance: int = 3, limit: int = 10) -> list[Track]:
        """Finds catalog tracks within a specific BPM range."""
        key = self.api_key or os.getenv("FREQBLOG_API_KEY")
        if not key or self._circuit_broken:
            return []

        res = self._call("GET", "/bpm", params={"bpm": bpm, "tolerance": tolerance, "limit": limit})
        tracks = []
        raw = res.get("tracks", [])
        if isinstance(raw, list):
            for item in raw:
                title = item.get("track_name", "")
                artist = item.get("artist_name", "")
                if title and artist:
                    tracks.append(Track(
                        title=title,
                        artist=artist,
                        source="freqblog_bpm"
                    ))
        return tracks

    def score_transition(self, song_a: str, song_b: str) -> dict:
        """
        Scores harmonic transition between two songs (0-100) using Camelot wheel
        key proximity, tempo alignment, and energy flow.
        """
        key = self.api_key or os.getenv("FREQBLOG_API_KEY")
        if not key or self._circuit_broken:
            return {}

        meta_a = parse_song_meta(song_a)
        meta_b = parse_song_meta(song_b)
        lookup_a = self.lookup(meta_a.get("song", song_a), meta_a.get("artist", ""))
        lookup_b = self.lookup(meta_b.get("song", song_b), meta_b.get("artist", ""))

        id_a = lookup_a.get("itunes_track_id")
        id_b = lookup_b.get("itunes_track_id")
        if not id_a or not id_b:
            return {"error": "One or both tracks could not be resolved in the catalog."}

        res = self._call("GET", "/transition", params={"from_track_id": id_a, "to_track_id": id_b})
        return res

    def get_harmonic_radio(self, seed_song: str, count: int = 10) -> list[Track]:
        """
        Generates a continuous DJ-style set respecting Camelot wheel harmonic
        adjacency and BPM continuity.
        """
        key = self.api_key or os.getenv("FREQBLOG_API_KEY")
        if not key or self._circuit_broken:
            return []

        meta = parse_song_meta(seed_song)
        lookup_data = self.lookup(meta.get("song", seed_song), meta.get("artist", ""))
        seed_id = lookup_data.get("itunes_track_id")
        if not seed_id:
            return []

        res = self._call("GET", "/radio", params={"seed_track_id": seed_id, "n": min(20, count)})
        tracks = []
        raw = res.get("tracks", [])
        if isinstance(raw, list):
            for item in raw:
                title = item.get("track_name", "")
                artist = item.get("artist_name", "")
                if title and artist:
                    tracks.append(Track(
                        title=title,
                        artist=artist,
                        source="freqblog_harmonic_radio"
                    ))
        return tracks

