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

    def get_charts(self, genre: str = None) -> list[Track]:
        tracks = []

        for chart_name, url in self.CHARTS:
            try:
                print(f"  └─ 🎧 [Beatport]: Fetching {chart_name} via stealth client...")
                soup = fetch_soup(url, timeout=10)
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

        # If Beatport was blocked by Cloudflare or yielded no tracks, fall back to
        # Deezer electronic club charts so recommendations never return empty.
        if not tracks:
            print("  └─ 🔄 [Beatport Fallback]: Beatport unavailable/blocked; fetching Deezer electronic charts...")
            try:
                from src.skills.music.aggregators.deezer import DeezerAggregator
                target_genre = genre or "electronic"
                deezer_tracks = DeezerAggregator().get_charts(genre=target_genre)
                for dt in deezer_tracks:
                    tracks.append(Track(
                        title=dt.title,
                        artist=dt.artist,
                        genre=dt.genre or "Electronic",
                        album=dt.album,
                        source="beatport_fallback_deezer"
                    ))
            except Exception as fallback_err:
                print(f"  └─ ⚠️ [Beatport Fallback Error]: {fallback_err}")

        return tracks

    def get_similar(self, query: str) -> list[Track]:
        # Beatport charts serve as the electronic music safety net
        return self.get_charts(genre="techno")
