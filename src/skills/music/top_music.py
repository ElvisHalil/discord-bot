import random
from typing import Optional
from src.skills.music.aggregators.manager import MusicAggregatorManager
from src.skills.music.templates import SONG_TEMPLATE

manager = MusicAggregatorManager()

def skill_top_music(
    genre: Optional[str] = None,
    similar_to: Optional[str] = None,
    match_mood: Optional[bool] = False,
    artist: Optional[str] = None
) -> str:
    """
    Fetches the current top trending/charting music in the world right now, finds similar songs, OR searches top tracks by an artist.
    ALWAYS use this skill FIRST when a user asks for song recommendations, top hits, songs by a specific artist, or what music is popular today.

    Args:
        genre: Genre for top charts, e.g. 'pop', 'techno', 'rock', 'hip hop'. Leave empty for all genres.
        similar_to: Seed song to find similar tracks for, as 'Song Title by Artist Name'.
        match_mood: Set true to match the emotional mood/energy of the similar_to song rather than just its style.
        artist: Artist name to get their top popular tracks, e.g. 'Deadmau5' or 'Daft Punk'.
    """
    if artist:
        print(f"🎵 [Executing Top Music Skill] Artist: {artist}")
        try:
            tracks = manager.get_artist_tracks(artist)
            if not tracks:
                return f"No tracks found for artist '{artist}'."

            header = f"=== TOP TRACKS BY '{artist.title()}' ===\n\n"
            output = header
            for t in tracks[:15]:
                output += t.display_line() + "\n"

            output += (
                "\n=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
                "Select ONE best track from the list above, then call "
                "`skill_youtube_searcher(query='<Song Title> by <Artist Name>', is_music=True)` to get its verified link.\n\n"
                + SONG_TEMPLATE
            )
            return output
        except Exception as e:
            return f"Error finding tracks for artist '{artist}': {e}"

    if similar_to:
        print(f"🎵 [Executing Top Music Skill] Similar to: {similar_to} (match_mood={match_mood})")
        try:
            mood_tag = None
            if match_mood:
                tracks, mood_info = manager.get_mood_matched_tracks(similar_to)
                if mood_info.get("mood"):
                    mood_tag = mood_info["mood"].title()
            else:
                tracks = manager.get_similar_tracks(similar_to)

            if not tracks:
                return f"No similar songs found for '{similar_to}'."

            # Deduplicate by title & artist
            seen = set()
            unique_tracks = []
            for t in tracks:
                key = (t.title.lower(), t.artist.lower())
                if key not in seen:
                    seen.add(key)
                    unique_tracks.append(t)

            random.shuffle(unique_tracks)

            header = f"=== SONGS MATCHING MOOD OF '{similar_to}' ({mood_tag}) ===\n\n" if mood_tag else f"=== SONGS SIMILAR TO '{similar_to}' ===\n\n"
            output = header
            for t in unique_tracks[:15]:
                output += t.display_line() + "\n"

            output += (
                "\n=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
                "Select ONE best matching song from the list above, then call "
                "`skill_youtube_searcher(query='<Song Title> by <Artist Name>', is_music=True)` to get its verified link.\n\n"
                + SONG_TEMPLATE
            )
            return output
        except Exception as e:
            return f"Error finding similar songs: {e}"

    print(f"🎵 [Executing Top Music Skill] Genre: {genre or 'All'}")
    try:
        all_tracks = manager.get_top_charts(genre=genre)

        output = f"=== TODAY'S TOP CHARTING MUSIC ({genre.upper() if genre else 'ALL GENRES'}) ===\n\n"
        if all_tracks:
            # 1. Primary: Featured diverse selection (max 1 track per artist, randomized across providers)
            # This ensures the model immediately sees 15+ completely different artists and never gets anchored to one band!
            artist_seen = set()
            featured_diverse = []
            for t in all_tracks:
                clean_artist = t.artist.lower().strip()
                if clean_artist and clean_artist not in artist_seen:
                    artist_seen.add(clean_artist)
                    featured_diverse.append(t)

            random.shuffle(featured_diverse)
            featured_sample = featured_diverse[:15]

            output += f"[FEATURED DIVERSE SELECTION ({len(featured_sample)} Different Artists)]\n"
            for t in featured_sample:
                output += t.display_line() + "\n"
            output += "\n"

            # 2. Detailed sections by source
            sections = {}
            for t in all_tracks:
                sections.setdefault(t.source or "general", []).append(t)

            section_headers = {
                "lastfm_global": "LAST.FM GLOBAL CHARTS (Random 10 from Top 50)",
                "spotify_global": "SPOTIFY GLOBAL (Random 10 from Top 50)",
                "youtube_trending": "YOUTUBE TRENDING MUSIC (Random 10 from Top 50)",
                "radio_airplay": "US RADIO AIRPLAY (Random 10 from Top 50)",
                "beatport_top_100": "BEATPORT TOP 100 (Random 7 from 100)",
                "beatport_hype_100": "BEATPORT HYPE 100 (Random 7 from 100)",
                "beatport_best_tracks": "BEATPORT BEST TRACKS (Random 7 from 100)",
            }

            for source_key, track_list in sections.items():
                if source_key in section_headers:
                    header = section_headers[source_key]
                elif source_key.startswith("lastfm_"):
                    lastfm_tag = source_key.replace("lastfm_", "").replace("_", " ").upper()
                    header = f"LAST.FM - {lastfm_tag} (Top Trending)"
                elif source_key.startswith("itunes_"):
                    itunes_genre = source_key.replace("itunes_", "").upper()
                    header = f"APPLE MUSIC / ITUNES US - {itunes_genre} (Random 10 from Top 50)"
                else:
                    header = source_key.upper()

                output += f"[{header}]\n"
                for t in track_list:
                    output += t.display_line() + "\n"
                output += "\n"
        else:
            output += "No charting music found.\n"

        output += (
            "=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
            "Present these recommendations to the user naturally.\n"
            "CRITICAL ARTIST DIVERSITY RULE: Pick a fresh track with a DIFFERENT artist each time! "
            "Inspect the recent chat history and do NOT recommend the same artist that was previously played (e.g., if Pendulum or Deadmau5 was already played, choose another artist from the list). Never recommend the same artist twice in a row.\n"
            "If the user asked for a single song, pick EXACTLY ONE from the list and present only that one; return several only if they asked for multiple.\n"
            "For every song you present, call `skill_youtube_searcher(query='<Song Title> by <Artist Name>', is_music=True)` to get its real link.\n\n"
            + SONG_TEMPLATE
        )
        return output
    except Exception as e:
        return f"Error fetching top music: {e}"

