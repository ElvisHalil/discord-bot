import json
import random
import urllib.request
from bs4 import BeautifulSoup
from src.skills.music.aggregators.base import BaseMusicAggregator
from src.skills.music.models import Track

class BeatportAggregator(BaseMusicAggregator):
    """Electronic music chart aggregator scraping Beatport's Top 100, Hype 100, and Best Tracks."""

    CHARTS = [
        ("Top 100", "https://www.beatport.com/top-100"),
        ("Hype 100", "https://www.beatport.com/hype-100"),
        ("Best Tracks", "https://www.beatport.com/best-tracks-so-far")
    ]

    def get_charts(self, genre: str = None) -> list[Track]:
        tracks = []
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

        for chart_name, url in self.CHARTS:
            try:
                print(f"  └─ 🎧 [Beatport]: Scraping {chart_name}...")
                req = urllib.request.Request(url, headers=headers)
                html = urllib.request.urlopen(req, timeout=10).read()
                soup = BeautifulSoup(html, 'html.parser')
                script = soup.find('script', id='__NEXT_DATA__')
                if script:
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
                print(f"  └─ ⚠️ [Beatport]: Error scraping {chart_name}: {e}")

        return tracks

    def get_similar(self, query: str) -> list[Track]:
        # Beatport charts serve as the electronic music safety net
        return self.get_charts(genre="techno")

