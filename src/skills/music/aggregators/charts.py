import re
import random
import requests
from bs4 import BeautifulSoup
from src.skills.music.aggregators.base import BaseMusicAggregator
from src.skills.music.models import Track

GENRE_MAP = {
    "pop": 14,
    "rock": 21,
    "country": 6,
    "countrymusic": 6,
    "hiphop": 18,
    "rap": 18,
    "hiphoprap": 18,
    "randb": 15,
    "rb": 15,
    "soul": 15,
    "rbsoul": 15,
    "dance": 17,
    "electronic": 7,
    "edm": 7,
    "jazz": 11,
    "alternative": 20,
    "christian": 22,
    "gospel": 22,
    "kpop": 50,
    "latino": 12,
    "latin": 12,
    "classical": 5,
    "dubstep": 100014,
    "bass": 100015,
    "drumandbass": 1049,
    "drumnbass": 1049,
    "dnb": 1049,
    "jungle": 1049,
    "house": 1048,
    "techno": 1050,
    "trance": 1051,
    "afrohouse": 100049,
    "metal": 1153,
    "heavymetal": 1153,
    "deathmetal": 1149,
    "blackmetal": 1149,
    "hairmetal": 1151,
    "christianmetal": 1095,
    "Minimalism": 1767
}

class ChartsAggregator(BaseMusicAggregator):
    """Aggregator for global mainstream music charts (Spotify, YouTube, Radio via Kworb, and iTunes RSS)."""

    def get_similar(self, query: str) -> list[Track]:
        return []

    def get_charts(self, genre: str = None) -> list[Track]:
        tracks = []
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

        # 1. Global charts (Kworb) when no genre specified
        if not genre:
            # Spotify Global
            try:
                print("  └─ 🎧 [Charts]: Scraping Spotify Global Daily...")
                r = requests.get('https://kworb.net/spotify/country/global_daily.html', headers=headers, timeout=5)
                soup = BeautifulSoup(r.text, 'html.parser')
                all_rows = soup.select('table.sortable tbody tr')[:50]
                if all_rows:
                    for tr in random.sample(all_rows, min(10, len(all_rows))):
                        text = tr.find('td', class_='text').get_text(strip=True)
                        if " - " in text:
                            artist, title = text.split(" - ", 1)
                            tracks.append(Track(title=title.strip(), artist=artist.strip(), source="spotify_global"))
            except Exception as e:
                print(f"  └─ ⚠️ [Charts]: Error loading Spotify charts: {e}")

            # YouTube Trending
            try:
                print("  └─ 📺 [Charts]: Scraping YouTube Trending...")
                r = requests.get('https://kworb.net/youtube/trending.html', headers=headers, timeout=5)
                soup = BeautifulSoup(r.text, 'html.parser')
                all_rows = soup.select('table tbody tr')[:50]
                if all_rows:
                    for tr in random.sample(all_rows, min(10, len(all_rows))):
                        text = tr.find('td', class_='text').get_text(strip=True)
                        if " - " in text:
                            artist, title = text.split(" - ", 1)
                            tracks.append(Track(title=title.strip(), artist=artist.strip(), source="youtube_trending"))
            except Exception as e:
                print(f"  └─ ⚠️ [Charts]: Error loading YouTube charts: {e}")

            # Radio Airplay
            try:
                print("  └─ 📻 [Charts]: Scraping US Radio Airplay...")
                r = requests.get('https://kworb.net/radio/', headers=headers, timeout=5)
                soup = BeautifulSoup(r.text, 'html.parser')
                all_rows = soup.select('table tbody tr')[:50]
                if all_rows:
                    for tr in random.sample(all_rows, min(10, len(all_rows))):
                        text = tr.find('td', class_='text').get_text(strip=True)
                        if " - " in text:
                            artist, title = text.split(" - ", 1)
                            tracks.append(Track(title=title.strip(), artist=artist.strip(), source="radio_airplay"))
            except Exception as e:
                print(f"  └─ ⚠️ [Charts]: Error loading Radio charts: {e}")

        # 2. iTunes RSS genre feeds
        genre_list = [genre] if genre else [None]
        if genre:
            raw_genres = re.split(r',|\band\b', genre)
            genre_list = [g.strip() for g in raw_genres if g.strip()] or [genre]

        for g_name in genre_list:
            g_id = None
            if g_name:
                clean_genre = g_name.lower().replace("-", "").replace(" ", "").replace("&", "and").replace("'", "")
                g_id = GENRE_MAP.get(clean_genre)

            try:
                base_url = "https://itunes.apple.com/us/rss/topsongs/limit=50"
                if g_id:
                    base_url += f"/genre={g_id}"
                base_url += "/json"

                r = requests.get(base_url, timeout=5)
                data = r.json()
                entries = data.get("feed", {}).get("entry", [])
                if entries:
                    for entry in random.sample(entries, min(10, len(entries))):
                        title = entry.get("im:name", {}).get("label", "Unknown Song")
                        artist = entry.get("im:artist", {}).get("label", "Unknown Artist")
                        category = entry.get("category", {}).get("attributes", {}).get("label", g_name or "General")
                        tracks.append(Track(
                            title=title,
                            artist=artist,
                            genre=category,
                            source=f"itunes_{g_name or 'top'}"
                        ))
            except Exception as e:
                print(f"  └─ ⚠️ [Charts]: Error fetching iTunes charts for {g_name}: {e}")

        return tracks

