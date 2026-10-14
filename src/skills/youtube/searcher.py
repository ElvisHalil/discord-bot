import re
from typing import Optional
from src.skills.youtube.client import search_youtube
from src.skills.music.playback.youtube_music import resolve_youtube_music_link
from src.skills.music.templates import SONG_TEMPLATE

MAX_YOUTUBE_RESULTS = 5

def skill_youtube_searcher(query: str, max_results: Optional[int] = 1, is_music: Optional[bool] = False) -> str:
    """
    Searches YouTube for videos to WATCH (dance clips, tutorials, live sets, visual performances) or,
    in music mode, fetches the playback link for a specific song found via `skill_top_music`.

    Args:
        query: What to search for. In music mode use 'Song Title by Artist Name'.
        max_results: Number of results to return (default 1, max 5).
        is_music: Set true ONLY to get a YouTube Music link for a specific song. Leave false for videos.
    """
    try:
        max_results = max(1, min(int(max_results or 1), MAX_YOUTUBE_RESULTS))
    except (TypeError, ValueError):
        max_results = 1
    print(f"▶️ [Executing YouTube Search Skill]: {query} (max_results: {max_results}, is_music: {is_music})")

    try:
        # Detect visual dance, tutorial, choreography, or performance requests and route to video mode
        is_visual = bool(re.search(r'\b(?:dance|dancing|tanzen|tanz|tutorial|compilation|choreography|battle|crowd|performance|how to)\b', query, re.IGNORECASE))
        if is_music and is_visual:
            print(f"🎬 [YouTube Search Skill]: Query '{query}' involves dance/visual performance. Switching from music mode to video mode.")
            is_music = False

        # Music mode: delegate directly to specialized YouTube Music playback resolver
        if is_music:
            # Guard against generic genre / non-song queries (e.g. "phonk", "phonk song", "techno music", "dnb track")
            clean_q_low = re.sub(r'^(?:play\s+)?(?:a\s+|some\s+)?', '', query.strip(), flags=re.IGNORECASE).strip()
            generic_m = re.match(r'^([a-z0-9\s&]+?)\s+(?:song|track|music|beat|video|banger)s?$', clean_q_low, re.IGNORECASE)
            KNOWN_BARE_GENRES = {
                "phonk", "drift phonk", "synthwave", "retrowave", "darksynth", "hardstyle",
                "dnb", "drum and bass", "drum & bass", "drumnbass", "jungle", "neurofunk",
                "techno", "hard techno", "melodic techno", "house", "tech house", "deep house",
                "trance", "psytrance", "dubstep", "edm", "electronic", "ambient", "lofi", "lo-fi",
                "rap", "hip hop", "hip-hop", "trap", "drill", "uk drill", "german rap", "deutschrap",
                "rock", "metal", "heavy metal", "indie", "pop", "r&b", "rnb", "jazz", "classical",
                "banger", "chill", "melodic", "hard", "dark", "sad", "love", "workout",
            }
            if (" by " not in query.lower() and " - " not in query.lower()) and (generic_m or clean_q_low.lower() in KNOWN_BARE_GENRES):
                genre_term = generic_m.group(1).strip() if generic_m else clean_q_low
                print(f"🔄 [YouTube Search Skill]: Redirecting genre/vibe query '{query}' -> skill_top_music(genre='{genre_term}').")
                from src.skills.music.top_music import skill_top_music
                return skill_top_music(genre=genre_term)

            results = resolve_youtube_music_link(query, max_results=max_results)
            if not results:
                print(f"❌ [YouTube Search Skill]: No music tracks found for '{query}'.")
                return (
                    f"NO_TRACKS_FOUND: Could not find any embeddable YouTube Music link for '{query}' (all uploads blocked third-party Discord embeds or were unavailable).\n"
                    f"CRITICAL INSTRUCTIONS FOR YOUR RESPONSE:\n"
                    f"1. If you were picking a song from a `skill_top_music` list, immediately call `skill_youtube_searcher(query='<Another Song Title> by <Artist Name>', is_music=True)` for a DIFFERENT track from that list.\n"
                    f"2. Otherwise, do NOT output the '**Song:**' or '**Artist:**' template and do NOT output any URL or placeholder.\n"
                    f"3. Simply inform the user in a short conversational sentence that you could not find an embeddable playback link for that track on YouTube."
                )

            print(f"✅ [YouTube Search Skill]: Returning {len(results)} music link(s) for '{query}'")
            formatted_results = ""
            for i, r in enumerate(results):
                formatted_results += f"{i+1}. Song: {r['title']}\n   Artist: {r['artist']}\n   Link: {r['url']}\n\n"

            return f"""
=== YOUTUBE MUSIC RESULTS ===
{formatted_results}

{SONG_TEMPLATE}
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

