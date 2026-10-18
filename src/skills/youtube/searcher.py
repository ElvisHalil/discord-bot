import re
from typing import Optional
from src.skills.youtube.client import search_youtube
from src.skills.music.playback.youtube_music import (
    resolve_youtube_music_link,
    get_served_song_vids,
    record_served_song_vid,
    get_blocked_official_info,
)
from src.skills.music.templates import SONG_TEMPLATE
from src.skills.music.top_music import skill_top_music

MAX_YOUTUBE_RESULTS = 5

VIDEO_RESULTS_TEMPLATE = (
    "=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
    "Present the video(s) to the user.\n"
    "1. Paste the raw standard YouTube video link (https://www.youtube.com/watch?v=...) on its own blank line so Discord embeds the media player.\n"
    "2. Do NOT wrap the URL in markdown `[Title](link)` or `<>` brackets, and do NOT use `music.youtube.com` in video mode.\n"
    "3. If searching for a single video, present ONLY the best single link."
)


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
            # Guard against generic genre / non-song queries (e.g. "phonk", "phonk song", "techno music", "play a song")
            clean_q_low = re.sub(r'^(?:play\s+)?(?:me\s+)?(?:a\s+|some\s+)?', '', query.strip(), flags=re.IGNORECASE).strip()
            generic_m = re.match(r'^([a-z0-9\s&]+?)\s+(?:song|track|music|beat|video|banger)s?$', clean_q_low, re.IGNORECASE)
            BARE_RANDOM_SONG = {"song", "songs", "track", "tracks", "music", "random", "random song", "random track", "good", "good song", "something", "anything", ""}
            KNOWN_BARE_GENRES = {
                "phonk", "drift phonk", "synthwave", "retrowave", "darksynth", "hardstyle",
                "dnb", "drum and bass", "drum & bass", "drumnbass", "jungle", "neurofunk",
                "techno", "hard techno", "melodic techno", "house", "tech house", "deep house",
                "trance", "psytrance", "dubstep", "edm", "electronic", "ambient", "lofi", "lo-fi",
                "rap", "hip hop", "hip-hop", "trap", "drill", "uk drill", "german rap", "deutschrap",
                "rock", "metal", "heavy metal", "indie", "pop", "r&b", "rnb", "jazz", "classical",
                "banger", "chill", "melodic", "hard", "dark", "sad", "love", "workout",
            }
            if (" by " not in query.lower() and " - " not in query.lower()) and (
                clean_q_low.lower() in BARE_RANDOM_SONG or generic_m or clean_q_low.lower() in KNOWN_BARE_GENRES
            ):
                if clean_q_low.lower() in BARE_RANDOM_SONG or (generic_m and generic_m.group(1).strip().lower() in BARE_RANDOM_SONG):
                    print(f"🔄 [YouTube Search Skill]: Redirecting generic song request '{query}' -> skill_top_music().")
                    return skill_top_music()
                genre_term = generic_m.group(1).strip() if generic_m else clean_q_low
                print(f"🔄 [YouTube Search Skill]: Redirecting genre/vibe query '{query}' -> skill_top_music(genre='{genre_term}').")
                return skill_top_music(genre=genre_term)

            previously_served = get_served_song_vids(query)
            if previously_served:
                try:
                    results = resolve_youtube_music_link(query, max_results=max_results, exclude_ids=previously_served)
                except TypeError:
                    results = resolve_youtube_music_link(query, max_results=max_results)
            else:
                results = resolve_youtube_music_link(query, max_results=max_results)

            if not results:
                print(f"❌ [YouTube Search Skill]: No music tracks found for '{query}'.")
                blocked_info = get_blocked_official_info(query)
                if blocked_info:
                    reason_detail = (
                        f"Found the official YouTube upload(s) of '{blocked_info['song']}' by '{blocked_info['artist']}' "
                        f"(channel: '{blocked_info['author']}'), but the record label has disabled third-party playback/embedding on Discord for all real uploads of this song."
                    )
                elif previously_served:
                    reason_detail = (
                        f"Searched for alternative uploads of '{query}' (excluding previously served ID(s): {', '.join(sorted(previously_served))}), "
                        f"but no other verified, Discord-embeddable upload exists on YouTube."
                    )
                else:
                    reason_detail = f"Could not find any embeddable YouTube Music link for '{query}' (all uploads blocked third-party Discord embeds or were unavailable)."
                return (
                    f"NO_TRACKS_FOUND: {reason_detail}\n"
                    f"CRITICAL INSTRUCTIONS FOR YOUR RESPONSE:\n"
                    f"1. If you were picking a song from a `skill_top_music` list, immediately call `skill_youtube_searcher(query='<Another Song Title> by <Artist Name>', is_music=True)` for a DIFFERENT track from that list.\n"
                    f"2. Otherwise, do NOT output the '**Song:**' or '**Artist:**' template and do NOT output any URL or placeholder.\n"
                    f"3. Simply inform the user in a short conversational sentence that the record label has disabled Discord playback for that track on YouTube (or that no working alternative upload exists)."
                )

            print(f"✅ [YouTube Search Skill]: Returning {len(results)} music link(s) for '{query}'")
            formatted_results = ""
            for i, r in enumerate(results):
                if r.get("id"):
                    record_served_song_vid(r.get("title", ""), r.get("artist", ""), r["id"])
                    record_served_song_vid(query, "", r["id"])
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

        return f"""
=== YOUTUBE VIDEO RESULTS ===
{formatted_results}

{VIDEO_RESULTS_TEMPLATE}
"""

    except Exception as e:
        print(f"❌ [YouTube Search Skill Error]: {e}")
        return f"Error searching YouTube: {e}. CRITICAL: Do NOT guess or invent a YouTube link! Tell the user the search failed."

