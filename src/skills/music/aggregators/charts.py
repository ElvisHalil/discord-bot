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
    "drill": 18,
    "grime": 18,
    "trap": 18,
    "randb": 15,
    "rnb": 15,
    "rb": 15,
    "soul": 15,
    "rbsoul": 15,
    "funk": 15,
    "dance": 17,
    "electronic": 7,
    "edm": 7,
    "jazz": 11,
    "alternative": 20,
    "indie": 20,
    "punk": 20,
    "christian": 22,
    "gospel": 22,
    "kpop": 50,
    "jpop": 27,
    "anime": 29,
    "latino": 12,
    "latin": 12,
    "reggaeton": 12,
    "salsa": 12,
    "classical": 5,
    "blues": 2,
    "reggae": 24,
    "soundtrack": 16,
    "dubstep": 100014,
    "bass": 100015,
    "drumandbass": 1049,
    "drumnbass": 1049,
    "dnb": 1049,
    "jungle": 1049,
    "house": 1048,
    "techhouse": 1048,
    "deephouse": 1048,
    "techno": 1050,
    "trance": 1051,
    "afrohouse": 100049,
    "afrobeats": 19,
    "world": 19,
    "metal": 1153,
    "heavymetal": 1153,
    "deathmetal": 1149,
    "blackmetal": 1149,
    "hairmetal": 1151,
    "christianmetal": 1095,
    "Minimalism": 1767,
    "minimalism": 1767,
}

# Maps nationality/country modifiers to iTunes 2-letter storefront codes
REGION_MODIFIERS = {
    "german": "de",
    "deutsch": "de",
    "germany": "de",
    "uk": "gb",
    "british": "gb",
    "english": "gb",
    "french": "fr",
    "france": "fr",
    "japanese": "jp",
    "japan": "jp",
    "korean": "kr",
    "korea": "kr",
    "spanish": "es",
    "spain": "es",
    "mexican": "mx",
    "mexico": "mx",
    "brazilian": "br",
    "brazil": "br",
    "italian": "it",
    "italy": "it",
    "swedish": "se",
    "sweden": "se",
    "dutch": "nl",
    "netherlands": "nl",
    "canadian": "ca",
    "canada": "ca",
    "australian": "au",
    "australia": "au",
    "turkish": "tr",
    "turkey": "tr",
    "indian": "in",
    "india": "in",
    "nigerian": "za",
    "southafrican": "za",
    "american": "us",
    "us": "us",
}

# Compound regional-genre shorthand terms (e.g. "deutschrap" -> de + rap)
COMPOUND_REGIONAL_GENRES = {
    "deutschrap": ("de", 18),
    "jpop": ("jp", 27),
    "jrock": ("jp", 21),
    "kpop": ("kr", 50),
    "krap": ("kr", 18),
    "khiphop": ("kr", 18),
    "khop": ("kr", 18),
    "ukdrill": ("gb", 18),
    "ukgrime": ("gb", 18),
    "ukgarage": ("gb", 17),
    "ukg": ("gb", 17),
    "frenchhouse": ("fr", 1048),
    "frenchtouch": ("fr", 7),
    "bailefunk": ("br", 17),
    "brazilianfunk": ("br", 17),
    "bollywood": ("in", 14),
    "punjabi": ("in", 14),
    "afrobeats": ("za", 19),
    "amapiano": ("za", 100049),
}

# Default multi-region storefront pools for genres when no country modifier is given
GENRE_REGION_MAP = {
    "dnb": ["gb", "us", "de"],
    "drumandbass": ["gb", "us", "de"],
    "drumnbass": ["gb", "us", "de"],
    "jungle": ["gb", "us"],
    "dubstep": ["gb", "us"],
    "house": ["gb", "us", "de", "nl"],
    "techhouse": ["gb", "us", "de", "es"],
    "deephouse": ["gb", "de", "nl", "za"],
    "dance": ["gb", "us", "de"],
    "electronic": ["gb", "de", "us", "fr"],
    "edm": ["us", "gb", "nl", "de"],
    "techno": ["de", "gb", "nl", "us"],
    "trance": ["nl", "de", "gb", "us"],
    "metal": ["de", "gb", "se", "us"],
    "heavymetal": ["de", "gb", "se", "us"],
    "deathmetal": ["se", "de", "us", "gb"],
    "blackmetal": ["se", "de", "gb"],
    "kpop": ["kr", "jp", "us"],
    "jpop": ["jp", "us"],
    "latin": ["mx", "es", "br", "us"],
    "latino": ["mx", "es", "br", "us"],
    "reggaeton": ["mx", "es", "us"],
    "afrohouse": ["za", "gb", "fr"],
}


def resolve_genre_and_regions(g_name: str | None) -> tuple[int | None, list[str]]:
    """
    Resolves a genre string into (itunes_genre_id, list_of_storefront_regions).
    Safeguard: Nationality keywords (like 'german', 'english', 'american') only strip
    when the remaining words match a valid known genre in GENRE_MAP, preventing song titles
    containing nationality words from being misparsed.
    """
    if not g_name or g_name.lower().strip() in ("all", "global", "general", "none"):
        return None, ["us", "gb"]

    clean_full = re.sub(r"[^a-z0-9]", "", g_name.lower().replace("&", "and"))

    # 1. Check compound regional genres first (e.g. 'deutschrap', 'kpop', 'jrock', 'ukdrill')
    if clean_full in COMPOUND_REGIONAL_GENRES:
        region, gid = COMPOUND_REGIONAL_GENRES[clean_full]
        return gid, [region]

    # 2. Check direct match in GENRE_MAP
    if clean_full in GENRE_MAP:
        gid = GENRE_MAP[clean_full]
        regions = GENRE_REGION_MAP.get(clean_full, ["us", "gb"])
        return gid, regions

    # 3. Check for multi-word nationality + genre combination (e.g. 'german rap', 'french pop', 'uk drill')
    tokens = [re.sub(r"[^a-z0-9]", "", w) for w in g_name.lower().replace("&", "and").split()]
    tokens = [t for t in tokens if t and t not in ("music", "song", "songs", "tracks", "track")]

    if len(tokens) >= 2:
        # Check two-word region prefix (e.g. 'south african house')
        if len(tokens) >= 3 and (tokens[0] + tokens[1]) in REGION_MODIFIERS:
            reg_code = REGION_MODIFIERS[tokens[0] + tokens[1]]
            rem_clean = "".join(tokens[2:])
            if rem_clean in GENRE_MAP:
                return GENRE_MAP[rem_clean], [reg_code]

        # Check single-word region modifier at start or end (e.g. 'german rap' or 'rap german')
        if tokens[0] in REGION_MODIFIERS:
            reg_code = REGION_MODIFIERS[tokens[0]]
            rem_clean = "".join(tokens[1:])
            if rem_clean in GENRE_MAP:
                return GENRE_MAP[rem_clean], [reg_code]
        if tokens[-1] in REGION_MODIFIERS:
            reg_code = REGION_MODIFIERS[tokens[-1]]
            rem_clean = "".join(tokens[:-1])
            if rem_clean in GENRE_MAP:
                return GENRE_MAP[rem_clean], [reg_code]

    # Unrecognized genre or non-genre phrase: do not guess or strip
    return None, []


class ChartsAggregator(BaseMusicAggregator):
    """Aggregator for global mainstream music charts (Spotify, YouTube, Radio via Kworb, and Multi-Region iTunes RSS)."""

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

        # 2. Multi-Region & Nationality-Aware iTunes RSS genre feeds
        genre_list = [genre] if genre else [None]
        if genre and genre.lower().strip() not in ("drum and bass", "r and b", "hip hop and rap", "melodic house and techno"):
            raw_genres = re.split(r',|\band\b', genre)
            genre_list = [g.strip() for g in raw_genres if g.strip()] or [genre]

        for g_name in genre_list:
            g_id, candidate_regions = resolve_genre_and_regions(g_name)
            if g_name and g_id is None:
                # Specific genre was requested that iTunes RSS does not index; skip so we don't pollute with US Pop
                continue

            selected_regions = random.sample(candidate_regions, min(2, len(candidate_regions))) if len(candidate_regions) > 2 else candidate_regions
            pooled_entries = []

            for region in selected_regions:
                try:
                    base_url = f"https://itunes.apple.com/{region}/rss/topsongs/limit=50"
                    if g_id:
                        base_url += f"/genre={g_id}"
                    base_url += "/json"

                    r = requests.get(base_url, timeout=5)
                    data = r.json()
                    entries = data.get("feed", {}).get("entry", [])
                    if isinstance(entries, dict):
                        entries = [entries]
                    if entries:
                        for entry in entries:
                            pooled_entries.append((region, entry))
                except Exception as e:
                    print(f"  └─ ⚠️ [Charts]: Error fetching iTunes ({region}) charts for {g_name}: {e}")

            if pooled_entries:
                random.shuffle(pooled_entries)
                artist_seen = set()
                sampled_count = 0
                for region, entry in pooled_entries:
                    title = entry.get("im:name", {}).get("label", "Unknown Song")
                    artist = entry.get("im:artist", {}).get("label", "Unknown Artist")
                    clean_art = artist.lower().strip()
                    if clean_art in artist_seen:
                        continue
                    artist_seen.add(clean_art)
                    category = entry.get("category", {}).get("attributes", {}).get("label", g_name or "General")
                    tracks.append(Track(
                        title=title,
                        artist=artist,
                        genre=category,
                        source=f"itunes_{g_name or 'top'}"
                    ))
                    sampled_count += 1
                    if sampled_count >= 10:
                        break

        return tracks


