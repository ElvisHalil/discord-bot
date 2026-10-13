import re
import random
import urllib.parse
import requests
from src.skills.music.aggregators.base import BaseMusicAggregator
from src.skills.music.models import Track

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
                if len(related_artists) > 12:
                    related_artists = random.sample(related_artists, 12)
                for rel_artist in related_artists:
                    try:
                        r_top = requests.get(f"https://api.deezer.com/artist/{rel_artist['id']}/top?limit=5", timeout=5)
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
                    except Exception:
                        continue
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
        genre_id = 0
        if genre:
            genre_id = self.GENRE_MAP.get(genre.lower().strip(), 0)

        endpoint = f"https://api.deezer.com/chart/{genre_id}/tracks?limit=25" if genre_id else "https://api.deezer.com/chart/0/tracks?limit=25"
        try:
            print(f"  └─ 🎧 [Deezer Charts]: Fetching {'genre ' + str(genre) if genre else 'Global'} charts...")
            r = requests.get(endpoint, timeout=5)
            data = r.json().get("data", [])
            tracks = []
            for t in data:
                tracks.append(Track(
                    title=t.get("title", "Unknown"),
                    artist=t.get("artist", {}).get("name", "Unknown"),
                    album=t.get("album", {}).get("title"),
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

