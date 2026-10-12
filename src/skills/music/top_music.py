import random
from src.skills.music.aggregators.manager import MusicAggregatorManager

manager = MusicAggregatorManager()

def skill_top_music(genre: str = None, similar_to: str = None, match_mood: bool = False) -> str:
    """
    Fetches the current top trending/charting music in the world right now, OR finds similar songs.
    ALWAYS use this skill FIRST when a user asks for song recommendations, top hits, or what music is popular today.
    - To get top trending charts by genre, specify 'genre' (e.g., 'pop, techno').
    - To get similar songs, specify 'similar_to' with the track and artist name (e.g. 'Believe by Cher').
    - To specifically match the emotional mood, vibe, or energy of a song, set 'match_mood=True' (e.g. 'match the mood of Blinding Lights').
    """
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

            output += """
=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===
Select ONE best matching song from the list above.
CRITICAL INSTRUCTION: Call `skill_youtube_searcher` with `is_music=True` and query="<Song Title> by <Artist Name>" to fetch its verified YouTube Music link.
When presenting the song to the user, you MUST format your response using this EXACT clean, text-only template (NO EMOJIS):

[Your brief conversational intro or similarity explanation, if any]

**Song:** <Song Title>
**Artist:** <Artist Name>
**Genre:** <Genre Name> (optional, include only if known)
**Album:** <Album Name> (optional, include only if known)

<Raw YouTube Music URL on its own blank line>

CRITICAL RULES:
1. BOTH '**Song:**' and '**Artist:**' MUST always be displayed.
2. Do NOT include ANY emojis in the song template lines. Keep it clean and text-only.
3. NEVER invent or hallucinate YouTube URLs. You MUST use `skill_youtube_searcher` to get the link.
4. Paste ONLY the bare YouTube Music URL on a new blank line at the bottom.
5. NEVER wrap the link in markdown (like `[Title](url)`) or `<>` brackets.
6. Do NOT output ANY markdown links like `[YouTube](...)` or `[Channel](...)` or `[Title](...)`.
"""
            return output
        except Exception as e:
            return f"Error finding similar songs: {e}"

    print(f"🎵 [Executing Top Music Skill] Genre: {genre or 'All'}")
    try:
        all_tracks = manager.get_top_charts(genre=genre)

        output = f"=== TODAY'S TOP CHARTING MUSIC ({genre.upper() if genre else 'ALL GENRES'}) ===\n\n"
        if all_tracks:
            # Group tracks by source section to preserve provider clarity
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

        output += """=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===
You have successfully fetched today's top music.
Present these lists to the user naturally.
If this skill returned NO data or an unrecognizable genre, immediately use your `search_web` tool to find the top music for that specific genre online.
CRITICAL INSTRUCTION: If the user explicitly asked for "a song" or "a beat" (singular), you MUST pick EXACTLY ONE song from the list, use `skill_youtube_searcher` with `is_music=True` to get its link, and present ONLY THAT ONE SONG. Do not return 3 songs. Only return multiple if they asked for a playlist or multiple recommendations.
CRITICAL INSTRUCTION: NEVER guess or invent YouTube URLs. You MUST call `skill_youtube_searcher` with `is_music=True` to get the real, working video link for any song you present.

When presenting a song to the user, you MUST format your response using this EXACT clean, text-only template (NO EMOJIS):

[Your brief conversational intro or recommendation context, if any]

**Song:** <Song Title>
**Artist:** <Artist Name>
**Genre:** <Genre Name> (optional, include only if known)
**Album:** <Album Name> (optional, include only if known)

<Raw YouTube Music URL on its own blank line>

CRITICAL RULES:
1. BOTH '**Song:**' and '**Artist:**' MUST always be displayed.
2. Do NOT include ANY emojis in the song template lines. Keep it clean and text-only.
3. Paste ONLY the bare YouTube Music URL on a new blank line at the bottom.
4. NEVER wrap the link in markdown (like `[Title](url)`) or `<>` brackets, otherwise Discord will fail to embed the music player!
5. Do NOT output ANY markdown links like `[YouTube](...)` or `[Channel](...)` or `[Title](...)`.
"""
        return output
    except Exception as e:
        return f"Error fetching top music: {e}"

