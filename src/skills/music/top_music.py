import random
from typing import Optional
from src.skills.music.aggregators.manager import MusicAggregatorManager
from src.skills.music.moods import parse_mood_and_genre
from src.skills.music.models import Track
from src.skills.music.playback.youtube_music import resolve_youtube_music_link
from src.skills.music.utils import MUSIC_METADATA_CACHE
from src.skills.music.templates import SONG_TEMPLATE

manager = MusicAggregatorManager()


def _is_manager_mocked() -> bool:
    """Detects if `manager` or its methods are mocked in unit tests so offline tests skip live YT lookups."""
    if hasattr(resolve_youtube_music_link, "assert_called"):
        return False
    return (
        hasattr(manager, "assert_called")
        or hasattr(manager.get_top_charts, "assert_called")
        or hasattr(manager.get_similar_tracks, "assert_called")
        or hasattr(manager.get_mood_tracks, "assert_called")
        or hasattr(manager.get_artist_tracks, "assert_called")
        or any(
            hasattr(getattr(agg, "get_similar", None), "assert_called")
            or hasattr(getattr(agg, "get_charts", None), "assert_called")
            for agg in getattr(manager, "aggregators", {}).values()
        )
    )


def _pre_resolve_top_candidate(candidates: list[Track]) -> str:
    """
    Pre-resolves a verified, Discord-embeddable YouTube Music link for the top candidate track
    (trying up to 4 candidates if one has Discord embedding disabled by its label).
    Eliminates an entire extra LLM tool-call round-trip on single-song discovery requests.
    """
    if not candidates or _is_manager_mocked():
        return ""

    from src.skills.music.utils import is_recently_played, record_played_track, RECENTLY_PLAYED_TRACKS, _norm_track_token

    # Filter out any tracks that have already been played in this session, and deprioritize the last played artist
    last_artist = RECENTLY_PLAYED_TRACKS[-1][1] if RECENTLY_PLAYED_TRACKS else ""
    fresh_diff_artist = [
        c for c in candidates
        if not is_recently_played(c.title, c.artist) and (not last_artist or _norm_track_token(c.artist) != last_artist)
    ]
    fresh_any = [c for c in candidates if not is_recently_played(c.title, c.artist)]
    ordered_candidates = fresh_diff_artist or fresh_any or candidates

    for cand in ordered_candidates[:4]:
        query = f"{cand.title} by {cand.artist}"
        try:
            results = resolve_youtube_music_link(query, max_results=1)
            if results:
                r = results[0]
                record_played_track(cand.title, cand.artist)
                record_played_track(r["title"], r["artist"])
                vid = r.get("id")
                genre = cand.genre or r.get("genre")
                album = cand.album or r.get("album")
                if vid and vid in MUSIC_METADATA_CACHE:
                    if genre and not MUSIC_METADATA_CACHE[vid].get("genre"):
                        MUSIC_METADATA_CACHE[vid]["genre"] = genre
                    if album and not MUSIC_METADATA_CACHE[vid].get("album"):
                        MUSIC_METADATA_CACHE[vid]["album"] = album

                lines = [
                    "\n=== VERIFIED YOUTUBE MUSIC RESULT (PRE-RESOLVED & READY TO PRESENT) ===",
                    f"Song: {r['title']}",
                    f"Artist: {r['artist']}",
                ]
                if genre:
                    lines.append(f"Genre: {genre}")
                if album:
                    lines.append(f"Album: {album}")
                lines.append(f"Link: {r['url']}\n")
                return "\n".join(lines)
        except Exception as e:
            print(f"  └─ ⚠️ [Top Music Pre-Resolver]: Error resolving '{query}': {e}")
            continue
    return ""


def skill_top_music(
    genre: Optional[str] = None,
    similar_to: Optional[str] = None,
    match_mood: Optional[bool] = False,
    artist: Optional[str] = None,
    mood: Optional[str] = None,
) -> str:
    """
    Fetches the current top trending/charting music, finds tracks matching a mood/vibe ('banger', 'melodic', 'hard', 'chill', 'sad', 'love'), finds similar songs, OR searches top tracks by an artist.
    ALWAYS use this skill FIRST when a user asks for genre recommendations, mood/vibe songs ('play me a banger', 'play something melodic', 'play something hard', 'play a love song'), top hits, songs by a specific artist, or similar songs.
    NOTE: When a user requests one specific song by title (e.g. 'play Strobe by Deadmau5'), call `skill_youtube_searcher(query='<Song> by <Artist>', is_music=True)` directly instead of passing the song title as a genre.

    Args:
        genre: Genre or regional style for top charts, e.g. 'pop', 'techno', 'dnb', 'german rap'. Leave empty for all genres.
        similar_to: Seed song to find similar tracks for, as 'Song Title by Artist Name'.
        match_mood: Set true to match the emotional mood/energy of the similar_to song rather than just its style.
        artist: Artist name to get their top popular tracks, e.g. 'Deadmau5' or 'Daft Punk'.
        mood: Mood or vibe keyword, e.g. 'banger', 'melodic', 'hard', 'dark', 'chill', 'sad', 'love', 'workout'.
    """
    if artist:
        print(f"🎵 [Executing Top Music Skill] Artist: {artist}")
        try:
            tracks = manager.get_artist_tracks(artist)
            if not tracks:
                return f"No tracks found for artist '{artist}'."

            random.shuffle(tracks)
            top_sample = tracks[:15]
            header = f"=== TOP TRACKS BY '{artist.title()}' ===\n\n"
            output = header
            pre_resolved = _pre_resolve_top_candidate(top_sample)
            if pre_resolved:
                output += pre_resolved + "\n"

            for t in top_sample:
                output += t.display_line() + "\n"

            output += (
                "\n=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
                "CRITICAL: If the user asked for a single song and `=== VERIFIED YOUTUBE MUSIC RESULT ===` is provided above, "
                "you MUST present THAT EXACT Song, Artist, and Link immediately using the template below—do NOT pick a different track from the list and do NOT call `skill_youtube_searcher` again! "
                "Only call `skill_youtube_searcher(query='<Song Title> by <Artist Name>', is_music=True)` if the user explicitly asked for multiple songs.\n\n"
                + SONG_TEMPLATE
            )
            return output
        except Exception as e:
            return f"Error finding tracks for artist '{artist}': {e}"

    parsed_genre, parsed_mood = parse_mood_and_genre(genre, mood)

    if similar_to:
        print(f"🎵 [Executing Top Music Skill] Similar to: {similar_to} (match_mood={match_mood}, mood={parsed_mood})")
        try:
            mood_tag = None
            if parsed_mood:
                tracks = manager.get_mood_tracks(mood=parsed_mood, genre=parsed_genre, similar_to=similar_to)
                mood_tag = parsed_mood.title()
            elif match_mood:
                tracks, mood_info = manager.get_mood_matched_tracks(similar_to)
                if mood_info.get("mood"):
                    mood_tag = mood_info["mood"].title()
            else:
                tracks = manager.get_similar_tracks(similar_to)

            if not tracks:
                return f"No similar songs found for '{similar_to}'."

            # Deduplicate by title & artist and prioritize artist diversity
            seen = set()
            unique_tracks = []
            for t in tracks:
                key = (t.title.lower(), t.artist.lower())
                if key not in seen:
                    seen.add(key)
                    unique_tracks.append(t)

            is_unrelated_fallback = all(t.source == "fallback_unrelated" for t in unique_tracks)
            random.shuffle(unique_tracks)
            top_sample = unique_tracks[:15]

            if is_unrelated_fallback:
                header = f"=== NO DIRECT SIMILAR SONGS FOUND FOR '{similar_to}' — FALLBACK SUGGESTIONS ===\n\n"
            elif mood_tag:
                header = f"=== SONGS MATCHING MOOD OF '{similar_to}' ({mood_tag}) ===\n\n"
            else:
                header = f"=== SONGS SIMILAR TO '{similar_to}' ===\n\n"

            output = header
            pre_resolved = _pre_resolve_top_candidate(top_sample)
            if pre_resolved:
                output += pre_resolved + "\n"

            for t in top_sample:
                output += t.display_line() + "\n"

            if is_unrelated_fallback:
                output += (
                    "\n=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
                    f"IMPORTANT: No directly related songs were found for '{similar_to}'. "
                    "Clearly let the user know that you couldn't find any directly related songs for that track, "
                    "and suggest ONE track from above that they might like instead. "
                    "If `=== VERIFIED YOUTUBE MUSIC RESULT ===` is provided above, present THAT EXACT track and its Link immediately without calling `skill_youtube_searcher` again; "
                    "otherwise call `skill_youtube_searcher(query='<Song Title> by <Artist Name>', is_music=True)`.\n\n"
                    + SONG_TEMPLATE
                )
            else:
                output += (
                    "\n=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
                    "CRITICAL: If the user asked for a single song and `=== VERIFIED YOUTUBE MUSIC RESULT ===` is provided above, "
                    "you MUST present THAT EXACT Song, Artist, and Link immediately using the template below—do NOT pick a different track from the list and do NOT call `skill_youtube_searcher` again! "
                    "Only call `skill_youtube_searcher(query='<Song Title> by <Artist Name>', is_music=True)` if the user explicitly asked for multiple songs.\n\n"
                    + SONG_TEMPLATE
                )
            return output
        except Exception as e:
            return f"Error finding similar songs: {e}"

    print(f"🎵 [Executing Top Music Skill] Genre: {parsed_genre or 'All'}, Mood: {parsed_mood or 'None'}")
    try:
        if parsed_mood:
            all_tracks = manager.get_mood_tracks(mood=parsed_mood, genre=parsed_genre)
            vibe_label = f"{parsed_mood.upper()} {parsed_genre.upper()}" if parsed_genre else f"{parsed_mood.upper()} (MULTI-GENRE MIX)"
            output = f"=== TOP VIBE / MOOD SELECTION: {vibe_label} ===\n\n"
        else:
            all_tracks = manager.get_top_charts(genre=parsed_genre)
            output = f"=== TODAY'S TOP CHARTING MUSIC ({parsed_genre.upper() if parsed_genre else 'ALL GENRES'}) ===\n\n"
        if all_tracks:
            # Featured diverse selection (max 1 track per artist, randomized across all providers)
            artist_seen = set()
            featured_diverse = []
            for t in all_tracks:
                clean_artist = t.artist.lower().strip()
                if clean_artist and clean_artist not in artist_seen:
                    artist_seen.add(clean_artist)
                    featured_diverse.append(t)

            random.shuffle(featured_diverse)
            featured_sample = featured_diverse[:15]

            pre_resolved = _pre_resolve_top_candidate(featured_sample)
            if pre_resolved:
                output += pre_resolved + "\n"

            output += f"[FEATURED DIVERSE SELECTION ({len(featured_sample)} Different Artists)]\n"
            for t in featured_sample:
                output += t.display_line() + "\n"
            output += "\n"
        else:
            output += "No charting music found.\n"

        output += (
            "=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
            "Present these recommendations to the user naturally.\n"
            "CRITICAL: If the user asked for a single song and `=== VERIFIED YOUTUBE MUSIC RESULT ===` is provided above, "
            "you MUST present THAT EXACT Song, Artist, and Link immediately using the template below—do NOT pick a different track from the list and do NOT call `skill_youtube_searcher` again!\n"
            "Only call `skill_youtube_searcher(query='<Song Title> by <Artist Name>', is_music=True)` if the user explicitly asked for multiple songs or if no pre-resolved link is shown above.\n\n"
            + SONG_TEMPLATE
        )
        return output
    except Exception as e:
        return f"Error fetching top music: {e}"

