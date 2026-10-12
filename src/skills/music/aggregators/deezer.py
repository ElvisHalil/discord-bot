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

    def get_charts(self, genre: str = None) -> list[Track]:
        return []
