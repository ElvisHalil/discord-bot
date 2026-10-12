import re
from src.skills.youtube.client import search_youtube
from src.skills.music.playback.youtube_music import resolve_youtube_music_link

def skill_youtube_searcher(query: str, max_results: int = 1, is_music: bool = False) -> str:
    """
    Searches YouTube for videos (dance clips, tutorials, music videos, or specific songs).
    - VIDEO MODE (is_music=False, default): Use when the user asks for videos to WATCH — such as dance clips (e.g. 'hakken dance', 'shuffle dance compilation'), dance tutorials ('how to hakken'), live festival sets, or visual performances. Returns standard playable YouTube video links.
    - MUSIC MODE (is_music=True): Use ONLY when fetching an audio playback link for a specific song/artist recommendation discovered via `skill_top_music`.
    IMPORTANT: If the user asks for dance videos, how people dance, or visual clips, use this tool directly in video mode (`is_music=False`).
    """
    print(f"▶️ [Executing YouTube Search Skill]: {query} (max_results: {max_results}, is_music: {is_music})")

    try:
        # Detect visual dance, tutorial, choreography, or performance requests and route to video mode
        is_visual = bool(re.search(r'\b(?:dance|dancing|tanzen|tanz|tutorial|compilation|choreography|battle|crowd|performance|how to)\b', query, re.IGNORECASE))
        if is_music and is_visual:
            print(f"🎬 [YouTube Search Skill]: Query '{query}' involves dance/visual performance. Switching from music mode to video mode.")
            is_music = False

        # Music mode: delegate directly to specialized YouTube Music playback resolver
        if is_music:
            # Guard against generic genre / non-song queries (e.g. "phonk song", "techno music", "dnb track")
            generic_m = re.match(r'^(?:a\s+|some\s+)?([a-z0-9\s]+?)\s+(?:song|track|music|beat|video)s?$', query.strip(), re.IGNORECASE)
            if generic_m and " by " not in query and " - " not in query:
                genre_term = generic_m.group(1).strip()
                print(f"⚠️ [YouTube Search Skill]: Blocked generic genre query '{query}'. Directing model to skill_top_music.")
                return (
                    f"GENRE_QUERY_REJECTED: '{query}' is a broad genre request, NOT a specific song title! "
                    f"You MUST call `skill_top_music(genre='{genre_term}')` FIRST to discover real songs in the '{genre_term}' genre. "
                    f"Then pick ONE specific song and call `skill_youtube_searcher(query='<Song Title> by <Artist Name>', is_music=True)`."
                )

            results = resolve_youtube_music_link(query, max_results=max_results)
            if not results:
                print(f"❌ [YouTube Search Skill]: No music tracks found for '{query}'.")
                return (
                    f"NO_TRACKS_FOUND: Could not find any verified YouTube Music link for '{query}'.\n"
                    f"CRITICAL INSTRUCTIONS FOR YOUR RESPONSE:\n"
                    f"1. Do NOT output the '**Song:**' or '**Artist:**' template.\n"
                    f"2. Do NOT output any URL, link, or placeholder (e.g. <Raw YouTube Music URL>).\n"
                    f"3. Simply inform the user in a short conversational sentence that you could not find the track on YouTube Music."
                )

            print(f"✅ [YouTube Search Skill]: Returning {len(results)} music link(s) for '{query}'")
            formatted_results = ""
            for i, r in enumerate(results):
                formatted_results += f"{i+1}. Song: {r['title']}\n   Artist: {r['artist']}\n   Link: {r['url']}\n\n"

            instruction_text = """=== REQUIRED RESPONSE TEMPLATE FOR SONGS ===
When presenting a song to the user, you MUST format your response using this EXACT clean, text-only template (NO EMOJIS):

[Your brief conversational intro or similarity explanation, if any]

**Song:** <Song Title>
**Artist:** <Artist Name>
**Genre:** <Genre Name> (optional, include only if known)
**Album:** <Album Name> (optional, include only if known)

<Raw YouTube Music URL on its own blank line>

CRITICAL RULES:
1. BOTH '**Song:**' and '**Artist:**' MUST always be displayed.
2. Do NOT include ANY emojis in the song template lines. Keep it clean and text-only.
3. Paste ONLY the bare YouTube Music URL on a new blank line at the bottom.
4. NEVER wrap the link in markdown (like `[Title](url)`) or `<>` brackets.
5. Do NOT output ANY markdown links like `[YouTube](...)` or `[Channel](...)` or `[Title](...)`.
"""
            return f"""
=== YOUTUBE MUSIC RESULTS ===
{formatted_results}

{instruction_text}
"""

        # General video mode: pure video search
        results = search_youtube(query, max_results=max_results)
        if not results:
            print(f"❌ [YouTube Search Skill]: No video results found for '{query}'.")
            return (
                f"NO_VIDEOS_FOUND: Could not find any playable YouTube video for '{query}'.\n"
                f"CRITICAL INSTRUCTIONS FOR YOUR RESPONSE:\n"
                f"1. Do NOT guess or invent any YouTube link or URL placeholder.\n"
                f"2. Simply tell the user in a short sentence that no matching video was found."
            )

        print(f"✅ [YouTube Search Skill]: Returning {len(results)} video link(s) for '{query}'")
        formatted_results = ""
        for i, r in enumerate(results):
            chan_str = f"\n   Channel: {r['channel']}" if r.get('channel') else ""
            formatted_results += f"{i+1}. Title: {r['title']}{chan_str}\n   Link: {r['url']}\n\n"

        instruction_text = """=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===
Present the video(s) to the user.
CRITICAL INSTRUCTION FOR DISCORD EMBEDS: To ensure a full media player is embedded directly in Discord, you MUST paste the raw standard YouTube video link (https://www.youtube.com/watch?v=...) on its own blank line. 
Do NOT wrap the link in markdown (like `[Title](link)`) or `<>` brackets, as that prevents Discord from creating the player embed. It MUST be a bare URL on a new line.
IMPORTANT: In video mode, you MUST use the standard YouTube domain (https://www.youtube.com/watch?v=...). Do NOT output a music.youtube.com link in video mode!
IMPORTANT: Do NOT output ANY markdown links like `[YouTube](...)` or `[Channel](...)` anywhere in your response. Only the raw URL.
If you searched for a specific song or video, present ONLY the best single link.
"""
        return f"""
=== YOUTUBE VIDEO RESULTS ===
{formatted_results}

{instruction_text}
"""

    except Exception as e:
        print(f"❌ [YouTube Search Skill Error]: {e}")
        return f"Error searching YouTube: {e}. CRITICAL: Do NOT guess or invent a YouTube link! Tell the user the search failed."

