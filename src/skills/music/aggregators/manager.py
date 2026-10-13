from typing import Dict
from src.skills.music.aggregators.base import BaseMusicAggregator
from src.skills.music.aggregators.lastfm import LastFmAggregator
from src.skills.music.aggregators.deezer import DeezerAggregator
from src.skills.music.aggregators.freqblog import FreqBlogAggregator
from src.skills.music.aggregators.beatport import BeatportAggregator
from src.skills.music.aggregators.charts import ChartsAggregator
from src.skills.music.models import Track
from src.skills.music.utils import is_recently_played, parse_song_meta, _norm_track_token

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

    def _filter_and_deduplicate(self, tracks: list[Track], exclude_query: str = None) -> list[Track]:
        """
        Unified exclusion and deduplication filter shared across all discovery modes:
        1. Deduplicates (title, artist) pairs across providers.
        2. Excludes the seed song when exclude_query is provided (e.g., similar_to).
        3. Excludes tracks in RECENTLY_PLAYED_TRACKS session history (falling back gracefully if all candidates would be removed).
        """
        if not tracks:
            return []

        seed_song_norm = ""
        seed_artist_norm = ""
        if exclude_query:
            meta = parse_song_meta(exclude_query)
            seed_song_norm = _norm_track_token(meta.get("song", ""))
            art_raw = meta.get("artist", "")
            if art_raw.lower() not in ("unknown artist", "unknown", ""):
                seed_artist_norm = _norm_track_token(art_raw)

        seen = set()
        deduped = []
        fresh = []

        for t in tracks:
            key = (t.title.lower().strip(), t.artist.lower().strip())
            if key in seen:
                continue
            seen.add(key)

            t_song_norm = _norm_track_token(t.title)
            t_art_norm = _norm_track_token(t.artist)

            # Exclude seed song in similarity queries
            if seed_song_norm and t_song_norm:
                if t_song_norm == seed_song_norm:
                    if not seed_artist_norm or not t_art_norm or seed_artist_norm in t_art_norm or t_art_norm in seed_artist_norm:
                        continue

            deduped.append(t)
            if not is_recently_played(t.title, t.artist):
                fresh.append(t)

        return fresh if fresh else deduped

    def get_similar_tracks(self, query: str) -> list[Track]:
        """
        Executes the similarity recommendation fallback chain:
        1. Query Last.fm API (scrobble-based similarity graph if API key configured)
        2. Query Deezer API (artist graph & related tracks)
        3. Query FreqBlog API (acoustic & neural embedding fallback if FREQBLOG_API_KEY configured)
        4. Transparent genre-aware / generic chart fallback tagged as 'fallback_unrelated' if song is completely unknown
        """
        tracks = []

        # 1. Primary: Last.fm (if LASTFM_API_KEY is available)
        lastfm = self.aggregators.get("lastfm")
        if lastfm:
            tracks = self._filter_and_deduplicate(lastfm.get_similar(query), exclude_query=query)

        # 2. Secondary: Deezer
        if not tracks:
            deezer = self.aggregators.get("deezer")
            if deezer:
                tracks = self._filter_and_deduplicate(deezer.get_similar(query), exclude_query=query)

        # 3. Tertiary: FreqBlog (Acoustic neural embedding fallback, protected by local cache)
        if not tracks:
            freqblog = self.aggregators.get("freqblog")
            if freqblog:
                tracks = self._filter_and_deduplicate(freqblog.get_similar(query), exclude_query=query)

        # 4. Fallback: Genre-aware or generic top charts when the track is completely unknown
        if not tracks:
            meta = parse_song_meta(query)
            detected_genre = meta.get("genre")
            print(f"  └─ ⚠️ [Aggregator]: No related songs found for '{query}'. Falling back to {'genre ' + detected_genre if detected_genre else 'general'} charts...")
            fallback_pool = self.get_top_charts(genre=detected_genre)
            if not fallback_pool:
                beatport = self.aggregators.get("beatport")
                if beatport:
                    fallback_pool = beatport.get_charts(genre=detected_genre or "techno")

            unrelated_tracks = []
            for t in fallback_pool:
                unrelated_tracks.append(Track(
                    title=t.title,
                    artist=t.artist,
                    genre=t.genre,
                    album=t.album,
                    url=t.url,
                    source="fallback_unrelated"
                ))
            tracks = self._filter_and_deduplicate(unrelated_tracks, exclude_query=query)

        return tracks

    def get_mood_matched_tracks(self, query: str) -> tuple[list[Track], dict]:
        """
        Specifically matches the acoustic mood, energy, and emotional feel of a seed song.
        Initiates with FreqBlog's audio intelligence, falling back gracefully to similarity chain.
        """
        freqblog = self.aggregators.get("freqblog")
        detected_mood = None
        detected_energy = None
        detected_valence = None

        if freqblog and hasattr(freqblog, "match_mood"):
            tracks, mood_info = freqblog.match_mood(query)
            tracks = self._filter_and_deduplicate(tracks, exclude_query=query)
            if tracks:
                return tracks, mood_info
            # If match_mood returned no tracks (e.g. 404 or unanalyzed track), check if lookup has seed metadata
            if hasattr(freqblog, "lookup"):
                meta = parse_song_meta(query)
                lookup_data = freqblog.lookup(meta.get("song", query), meta.get("artist", ""))
                if lookup_data:
                    detected_mood = lookup_data.get("mood")
                    detected_energy = lookup_data.get("energy")
                    detected_valence = lookup_data.get("valence")

        # Fallback to general similarity if mood match yields 0
        fallback_tracks = self.get_similar_tracks(query)
        mood_meta = {
            "seed_track": query,
            "mood": detected_mood or "similar",
            "energy": detected_energy,
            "valence": detected_valence,
        }
        return fallback_tracks, mood_meta

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

        # 3. Deezer charts (reliable public API with genre support)
        deezer = self.aggregators.get("deezer")
        if deezer:
            all_tracks.extend(deezer.get_charts(genre=genre))

        # 4. Global streaming and radio charts (Spotify, YouTube, Kworb, iTunes)
        charts = self.aggregators.get("charts")
        if charts:
            all_tracks.extend(charts.get_charts(genre=genre))

        return self._filter_and_deduplicate(all_tracks)

    def get_artist_tracks(self, artist: str) -> list[Track]:
        """Fetches top tracks by a specific artist using Deezer."""
        deezer = self.aggregators.get("deezer")
        if deezer and hasattr(deezer, "get_artist_top_tracks"):
            tracks = deezer.get_artist_top_tracks(artist)
            return self._filter_and_deduplicate(tracks)
        return []


