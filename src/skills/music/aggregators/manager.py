from typing import Dict
from src.skills.music.aggregators.base import BaseMusicAggregator
from src.skills.music.aggregators.lastfm import LastFmAggregator
from src.skills.music.aggregators.deezer import DeezerAggregator
from src.skills.music.aggregators.freqblog import FreqBlogAggregator
from src.skills.music.aggregators.beatport import BeatportAggregator
from src.skills.music.aggregators.charts import ChartsAggregator
from src.skills.music.models import Track

class MusicAggregatorManager:
    """
    Central manager for music recommendations and chart aggregators.
    Orchestrates the fallback cascade and allows seamless registration of new music providers.
    """

    def __init__(self):
        self.aggregators: Dict[str, BaseMusicAggregator] = {
            "lastfm": LastFmAggregator(),
            "deezer": DeezerAggregator(),
            "freqblog": FreqBlogAggregator(),
            "beatport": BeatportAggregator(),
            "charts": ChartsAggregator(),
        }

    def register_aggregator(self, name: str, aggregator: BaseMusicAggregator):
        """Register a new music recommendation or chart provider (e.g. Last.fm, Spotify)."""
        self.aggregators[name] = aggregator

    def get_similar_tracks(self, query: str) -> list[Track]:
        """
        Executes the similarity recommendation fallback chain:
        1. Query Last.fm API (scrobble-based similarity graph if API key configured)
        2. Query Deezer API (artist graph & related tracks)
        3. Query FreqBlog API (acoustic & neural embedding fallback if FREQBLOG_API_KEY configured)
        4. Fall back to Beatport charts safety net if previous yield no results
        """
        tracks = []

        # 1. Primary: Last.fm (if LASTFM_API_KEY is available)
        lastfm = self.aggregators.get("lastfm")
        if lastfm:
            tracks = lastfm.get_similar(query)

        # 2. Secondary: Deezer
        if not tracks:
            deezer = self.aggregators.get("deezer")
            if deezer:
                tracks = deezer.get_similar(query)

        # 3. Tertiary: FreqBlog (Acoustic neural embedding fallback, protected by local cache)
        if not tracks:
            freqblog = self.aggregators.get("freqblog")
            if freqblog:
                tracks = freqblog.get_similar(query)

        # 4. Fallback: Beatport electronic club charts safety net
        if not tracks:
            print("  └─ ⚠️ [Aggregator]: Primary similarity yielded no tracks, falling back to Beatport...")
            beatport = self.aggregators.get("beatport")
            if beatport:
                tracks = beatport.get_charts(genre="techno")

        return tracks

    def get_mood_matched_tracks(self, query: str) -> tuple[list[Track], dict]:
        """
        Specifically matches the acoustic mood, energy, and emotional feel of a seed song.
        Initiates with FreqBlog's audio intelligence, falling back gracefully to similarity chain.
        """
        freqblog = self.aggregators.get("freqblog")
        if freqblog and hasattr(freqblog, "match_mood"):
            tracks, mood_info = freqblog.match_mood(query)
            if tracks:
                return tracks, mood_info

        # Fallback to general similarity if mood match yields 0
        fallback_tracks = self.get_similar_tracks(query)
        return fallback_tracks, {"seed_track": query, "mood": "similar"}

    def get_top_charts(self, genre: str = None) -> list[Track]:
        """Fetches trending and charting music across Last.fm, Beatport, and global charts."""
        all_tracks = []

        # 1. Last.fm charts (global trending or genre tag)
        lastfm = self.aggregators.get("lastfm")
        if lastfm:
            all_tracks.extend(lastfm.get_charts(genre=genre))

        # 2. Beatport charts
        beatport = self.aggregators.get("beatport")
        if beatport:
            all_tracks.extend(beatport.get_charts(genre=genre))

        # 3. Global streaming and radio charts (Spotify, YouTube, Kworb, iTunes)
        charts = self.aggregators.get("charts")
        if charts:
            all_tracks.extend(charts.get_charts(genre=genre))

        return all_tracks

