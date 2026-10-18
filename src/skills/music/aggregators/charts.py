import re
import random
import time
import requests
from bs4 import BeautifulSoup
from src.skills.music.aggregators.base import BaseMusicAggregator
from src.skills.music.catalogs import (
    COMPOUND_REGIONAL_GENRES,
    GENRE_MAP,
    GENRE_REGION_MAP,
    REGION_MODIFIERS,
)
from src.skills.music.models import Track
from src.utils.workers import map_in_workers, submit_to_worker


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


_HTTP_SESSION = requests.Session()
_adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=20)
_HTTP_SESSION.mount("https://", _adapter)
_HTTP_SESSION.mount("http://", _adapter)

def _http_get(url: str, **kwargs):
    return _HTTP_SESSION.get(url, **kwargs)

_CHARTS_RAW_CACHE: dict[str, tuple[float, list]] = {}
_CHARTS_CACHE_TTL = 900.0

def clear_charts_cache() -> None:
    _CHARTS_RAW_CACHE.clear()

class ChartsAggregator(BaseMusicAggregator):
    """Aggregator for global mainstream music charts (Spotify, YouTube, Radio via Kworb, and Multi-Region iTunes RSS)."""

    def get_similar(self, query: str) -> list[Track]:
        return []

    def _fetch_kworb_rows(self, cache_key: str, url: str, selector: str, label: str) -> list[tuple[str, str]]:
        now = time.monotonic()
        if cache_key in _CHARTS_RAW_CACHE:
            ts, cached_rows = _CHARTS_RAW_CACHE[cache_key]
            if now - ts < _CHARTS_CACHE_TTL:
                return cached_rows

        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        rows_data = []
        try:
            print(f"  └─ {label}")
            r = _http_get(url, headers=headers, timeout=5)
            soup = BeautifulSoup(r.text, 'html.parser')
            all_rows = soup.select(selector)[:50]
            for tr in all_rows:
                td = tr.find('td', class_='text')
                if td:
                    text = td.get_text(strip=True)
                    if " - " in text:
                        artist, title = text.split(" - ", 1)
                        rows_data.append((title.strip(), artist.strip()))
            if rows_data:
                _CHARTS_RAW_CACHE[cache_key] = (now, rows_data)
        except Exception as e:
            print(f"  └─ ⚠️ [Charts]: Error loading {cache_key} charts: {e}")
        return rows_data

    def _fetch_itunes_region(self, region: str, g_id: int | None, g_name: str | None) -> list[tuple[str, dict]]:
        cache_key = f"itunes:{region}:{g_id or 0}"
        now = time.monotonic()
        if cache_key in _CHARTS_RAW_CACHE:
            ts, cached_entries = _CHARTS_RAW_CACHE[cache_key]
            if now - ts < _CHARTS_CACHE_TTL:
                return cached_entries

        entries_out = []
        try:
            base_url = f"https://itunes.apple.com/{region}/rss/topsongs/limit=50"
            if g_id:
                base_url += f"/genre={g_id}"
            base_url += "/json"

            r = _http_get(base_url, timeout=5)
            data = r.json()
            entries = data.get("feed", {}).get("entry", [])
            if isinstance(entries, dict):
                entries = [entries]
            if entries:
                entries_out = [(region, entry) for entry in entries]
                _CHARTS_RAW_CACHE[cache_key] = (now, entries_out)
        except Exception as e:
            print(f"  └─ ⚠️ [Charts]: Error fetching iTunes ({region}) charts for {g_name}: {e}")
        return entries_out

    def get_charts(self, genre: str = None) -> list[Track]:
        tracks = []

        # 1. Global charts (Kworb) when no genre specified — fetched concurrently
        if not genre:
            kworb_targets = [
                ("spotify_global", "https://kworb.net/spotify/country/global_daily.html", "table.sortable tbody tr", "🎧 [Charts]: Scraping Spotify Global Daily..."),
                ("youtube_trending", "https://kworb.net/youtube/trending.html", "table tbody tr", "📺 [Charts]: Scraping YouTube Trending..."),
                ("radio_airplay", "https://kworb.net/radio/", "table tbody tr", "📻 [Charts]: Scraping US Radio Airplay..."),
            ]
            futures = [
                (src, submit_to_worker(self._fetch_kworb_rows, src, url, sel, lbl))
                for src, url, sel, lbl in kworb_targets
            ]
            for src, fut in futures:
                rows = fut.result()
                if rows:
                    for title, artist in random.sample(rows, min(10, len(rows))):
                        tracks.append(Track(title=title, artist=artist, source=src))

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

            if len(selected_regions) == 1:
                pooled_entries.extend(self._fetch_itunes_region(selected_regions[0], g_id, g_name))
            elif selected_regions:
                for reg_entries in map_in_workers(lambda reg: self._fetch_itunes_region(reg, g_id, g_name), selected_regions):
                    pooled_entries.extend(reg_entries)

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


