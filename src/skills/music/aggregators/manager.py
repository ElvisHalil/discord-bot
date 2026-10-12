from typing import Dict
from src.skills.music.aggregators.base import BaseMusicAggregator
from src.skills.music.aggregators.deezer import DeezerAggregator
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
            "deezer": DeezerAggregator(),
            "beatport": BeatportAggregator(),
            "charts": ChartsAggregator(),
        }

    def register_aggregator(self, name: str, aggregator: BaseMusicAggregator):
        """Register a new music recommendation or chart provider (e.g. Last.fm, Spotify)."""
        self.aggregators[name] = aggregator

    def get_similar_tracks(self, query: str) -> list[Track]:
        """
        Executes the similarity recommendation fallback chain:
        1. Query Deezer API (artist graph & related tracks)
        2. Fall back to Beatport charts safety net if Deezer yields no results
        """
        # Primary: Deezer
        deezer = self.aggregators.get("deezer")
        tracks = deezer.get_similar(query) if deezer else []

        # Future: Can insert Last.fm check here if configured!

        # Fallback: Beatport electronic club charts
        if not tracks:
            print("  └─ ⚠️ [Aggregator]: Primary similarity yielded no tracks, falling back to Beatport...")
            beatport = self.aggregators.get("beatport")
            if beatport:
                tracks = beatport.get_charts(genre="techno")

        return tracks

    def get_top_charts(self, genre: str = None) -> list[Track]:
        """Fetches trending and charting music across Beatport and global charts."""
        all_tracks = []

        beatport = self.aggregators.get("beatport")
        if beatport:
            all_tracks.extend(beatport.get_charts(genre=genre))

        charts = self.aggregators.get("charts")
        if charts:
            all_tracks.extend(charts.get_charts(genre=genre))

        return all_tracks
