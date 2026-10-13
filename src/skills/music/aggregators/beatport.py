import json
import random
from src.skills.music.aggregators.base import BaseMusicAggregator
from src.skills.music.models import Track
from src.utils.http import fetch_soup

class BeatportAggregator(BaseMusicAggregator):
    """Electronic music chart aggregator fetching Beatport's Top 100, Hype 100, and Best Tracks
    via centralized stealth client, with automatic fallback to Deezer electronic charts."""

    CHARTS = [
        ("Top 100", "https://www.beatport.com/top-100"),
        ("Hype 100", "https://www.beatport.com/hype-100"),
        ("Best Tracks", "https://www.beatport.com/best-tracks-so-far")
    ]

    BEATPORT_GENRE_QUERIES = {
        "techno": "Beatport Top 100 Techno",
        "melodic techno": "Beatport Top 100 Melodic House & Techno",
        "hard techno": "Beatport Top 100 Hard Techno",
        "dnb": "Beatport Top 100 Drum & Bass",
        "drum and bass": "Beatport Top 100 Drum & Bass",
        "drumnbass": "Beatport Top 100 Drum & Bass",
        "drum & bass": "Beatport Top 100 Drum & Bass",
        "jungle": "Beatport Top 100 Drum & Bass",
        "house": "Beatport Top 100 House",
        "tech house": "Beatport Top 100 Tech House",
        "deep house": "Beatport Top 100 Deep House",
        "afro house": "Beatport Top 100 Afro House",
        "afrohouse": "Beatport Top 100 Afro House",
        "progressive house": "Beatport Top 100 Progressive House",
        "trance": "Beatport Top 100 Trance",
        "psytrance": "Beatport Top 100 Psy-Trance",
        "dubstep": "Beatport Top 100 Dubstep",
        "bass": "Beatport Top 100 Bass",
        "electro": "Beatport Top 100 Electro",
        "minimal": "Beatport Top 100 Minimal Deep Tech",
        "electronic": "Beatport Top 100",
        "edm": "Beatport Top 100",
        "dance": "Beatport Top 100",
    }

    def get_charts(self, genre: str = None) -> list[Track]:
        tracks = []
        clean_g = (genre or "").lower().strip()

        # If a non-electronic genre is requested (e.g., 'german rap', 'country', 'rock'),
        # skip direct Beatport scraping unless it is an electronic/Beatport genre or global chart
        if not clean_g or clean_g in self.BEATPORT_GENRE_QUERIES:
            for chart_name, url in self.CHARTS:
                try:
                    print(f"  └─ 🎧 [Beatport]: Fetching {chart_name} via stealth client...")
                    soup = fetch_soup(url, timeout=5)
                    script = soup.find('script', id='__NEXT_DATA__')
                    if script and script.string:
                        data = json.loads(script.string)
                        queries = data.get('props', {}).get('pageProps', {}).get('dehydratedState', {}).get('queries', [])
                        for q in queries:
                            if 'data' in q.get('state', {}) and 'results' in q['state']['data']:
                                results = q['state']['data']['results']
                                if results and 'name' in results[0]:
                                    sampled = random.sample(results, min(7, len(results)))
                                    for t in sampled:
                                        artist_names = ", ".join([a.get('name', '') for a in t.get('artists', [])])
                                        genre_name = t.get('genre', {}).get('name', 'Unknown')
                                        tracks.append(Track(
                                            title=t.get('name', 'Unknown'),
                                            artist=artist_names,
                                            genre=genre_name,
                                            source=f"beatport_{chart_name.lower().replace(' ', '_')}"
                                        ))
                                    break
                except Exception as e:
                    print(f"  └─ ⚠️ [Beatport]: Error fetching {chart_name}: {e}")

        # If Beatport was blocked by Cloudflare or yielded no tracks, resolve Beatport Top 100
        # genre playlists hosted on Deezer (or general genre playlists for non-electronic genres).
        if not tracks:
            try:
                from src.skills.music.aggregators.deezer import DeezerAggregator
                if not clean_g or clean_g in ("all", "global", "general", "none"):
                    target_query = "Beatport Top 100"
                    display_genre = "Electronic"
                elif clean_g in self.BEATPORT_GENRE_QUERIES:
                    target_query = self.BEATPORT_GENRE_QUERIES[clean_g]
                    display_genre = genre.title()
                else:
                    target_query = genre
                    display_genre = genre.title()

                print(f"  └─ 🔄 [Beatport via Deezer]: Fetching '{target_query}' playlists...")
                deezer_tracks = DeezerAggregator().get_charts(genre=target_query)
                for dt in deezer_tracks:
                    tracks.append(Track(
                        title=dt.title,
                        artist=dt.artist,
                        genre=display_genre if display_genre != "Electronic" else (dt.genre or "Electronic"),
                        album=dt.album,
                        source="beatport_fallback_deezer"
                    ))
            except Exception as fallback_err:
                print(f"  └─ ⚠️ [Beatport Fallback Error]: {fallback_err}")

        return tracks

    def get_similar(self, query: str) -> list[Track]:
        # Beatport charts serve as the electronic music safety net
        return self.get_charts(genre="techno")
