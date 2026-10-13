from src.skills.music.aggregators.base import BaseMusicAggregator
from src.skills.music.models import Track

class BeatportAggregator(BaseMusicAggregator):
    """Electronic music chart aggregator resolving Beatport Top 100, Hype 100, and Subgenre charts
    directly from Deezer-hosted Beatport playlists."""

    BEATPORT_GENRE_QUERIES = {
        "techno": "Beatport Top 100 Techno",
        "melodic techno": "Beatport Top 100 Melodic House & Techno",
        "melodic house": "Beatport Top 100 Melodic House & Techno",
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
        "hype": "Beatport Hype 100",
    }

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
            from src.skills.music.aggregators.deezer import DeezerAggregator
            print(f"  └─ 🎧 [Beatport via Deezer]: Fetching '{target_query}' playlists...")
            deezer_tracks = DeezerAggregator().get_charts(genre=target_query)
            for dt in deezer_tracks:
                tracks.append(Track(
                    title=dt.title,
                    artist=dt.artist,
                    genre=display_genre if display_genre != "Electronic" else (dt.genre or "Electronic"),
                    album=dt.album,
                    source="beatport_fallback_deezer"
                ))
        except Exception as err:
            print(f"  └─ ⚠️ [Beatport Playlist Error]: {err}")

        return tracks

    def get_similar(self, query: str) -> list[Track]:
        # Beatport charts serve as the electronic music safety net
        return self.get_charts(genre="techno")
