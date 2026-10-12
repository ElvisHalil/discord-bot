from src.skills.youtube.client import search_youtube
from src.skills.music.playback.youtube_music import resolve_youtube_music_link

def skill_youtube_searcher(query: str, max_results: int = 1, is_music: bool = False) -> str:
    """
    Use this skill ONLY to search for specific known videos, channels, or specific song titles on YouTube.
    IMPORTANT: If the user asks for 'top songs', 'trending music', or recommendations (even if they ask for YouTube links), DO NOT use this tool first. You MUST use the `skill_top_music` tool FIRST to discover the actual songs, and only use this YouTube tool afterward to fetch the links.
    If the user asks for a specific song or video, leave max_results as 1 (default).
    Set `is_music` to True ONLY if you are explicitly searching for a song or music track.
    """
    print(f"▶️ [Executing YouTube Search Skill]: {query} (max_results: {max_results}, is_music: {is_music})")

    try:
        # Music mode: delegate directly to specialized YouTube Music playback resolver
        if is_music:
            results = resolve_youtube_music_link(query, max_results=max_results)
            if not results:
                return f"Could not find music link for '{query}'. CRITICAL: Do NOT guess or invent a YouTube link! Tell the user you couldn't find it."

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
            return f"Could not find YouTube video for '{query}'. CRITICAL: Do NOT guess or invent a YouTube link! Tell the user you couldn't find it."

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
        return f"Error searching YouTube: {e}. CRITICAL: Do NOT guess or invent a YouTube link! Tell the user the search failed."

