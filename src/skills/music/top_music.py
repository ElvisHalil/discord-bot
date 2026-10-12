import random
from src.skills.music.aggregators.manager import MusicAggregatorManager

manager = MusicAggregatorManager()

def skill_top_music(genre: str = None, similar_to: str = None) -> str:
    """
    Fetches the current top trending/charting music in the world right now, OR finds similar songs.
    ALWAYS use this skill FIRST when a user asks for song recommendations, top hits, or what music is popular today.
    - To get top trending charts by genre, specify 'genre' (e.g., 'pop, techno').
    - To get similar songs, specify 'similar_to' with the track and artist name (e.g. 'Believe by Cher').
    """
    if similar_to:
        print(f"🎵 [Executing Top Music Skill] Similar to: {similar_to}")
        try:
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

            output = f"=== SONGS SIMILAR TO '{similar_to}' ===\n\n"
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
            sampled = random.sample(all_tracks, min(25, len(all_tracks)))
            for t in sampled:
                output += t.display_line() + "\n"
        else:
            output += "No charting music found.\n"

        output += """
=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===
Review the charting tracks above and present recommendations to the user.
To present a playable song link, call `skill_youtube_searcher` with `is_music=True` and format using the standard song template:

[Your conversational text]

**Song:** <Song Title>
**Artist:** <Artist Name>
**Genre:** <Genre Name> (optional)
**Album:** <Album Name> (optional)

<Raw YouTube Music URL on its own blank line>
"""
        return output
    except Exception as e:
        return f"Error fetching top music: {e}"
