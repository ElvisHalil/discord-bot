from src.skills.music.aggregators.base import BaseMusicAggregator
from src.skills.music.catalogs import BEATPORT_GENRE_QUERIES as _BEATPORT_GENRE_QUERIES
from src.skills.music.models import Track

class BeatportAggregator(BaseMusicAggregator):
    """Electronic music chart aggregator resolving Beatport Top 100, Hype 100, and Subgenre charts
    directly from Deezer-hosted Beatport playlists."""

    BEATPORT_GENRE_QUERIES = _BEATPORT_GENRE_QUERIES

    def get_charts(self, genre: str = None) -> list[Track]:
        tracks = []
        clean_g = (genre or "").lower().strip()

        if not clean_g or clean_g in ("all", "global", "general", "none"):
            target_query = "Beatport Top 100"
            display_genre = "Electronic"
        elif clean_g in self.BEATPORT_GENRE_QUERIES:
            target_query = self.BEATPORT_GENRE_QUERIES[clean_g]
            display_genre = genre.title()
        elif clean_g.startswith("beatport"):
            target_query = genre
            display_genre = "Electronic"
        else:
            # Skip non-electronic genres so we don't duplicate Deezer's general genre search
            return []

        try:
            from src.skills.music.aggregators.deezer import DeezerAggregator, clean_playlist_genre_label
            if display_genre == "Electronic" and target_query:
                display_genre = clean_playlist_genre_label(target_query)
            else:
                display_genre = clean_playlist_genre_label(display_genre)
            print(f"  └─ 🎧 [Beatport via Deezer]: Fetching '{target_query}' playlists...")
            deezer_tracks = DeezerAggregator().get_charts(genre=target_query)
            for dt in deezer_tracks:
                tracks.append(Track(
                    title=dt.title,
                    artist=dt.artist,
                    genre=display_genre if display_genre != "Electronic" else (clean_playlist_genre_label(dt.genre) if dt.genre else "Electronic"),
                    album=dt.album,
                    source="beatport_fallback_deezer",
                    deezer_id=dt.deezer_id,
                    preview_url=dt.preview_url,
                    tier=dt.tier,
                    relevance_score=dt.relevance_score,
                    verified_genre=True,
                ))
        except Exception as err:
            print(f"  └─ ⚠️ [Beatport Playlist Error]: {err}")

        return tracks

    def get_similar(self, query: str) -> list[Track]:
        # Beatport charts serve as the electronic music safety net
        return self.get_charts(genre="techno")
