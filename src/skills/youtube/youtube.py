import re
from typing import Optional
from src.skills.music.playback.youtube_music import (
    get_blocked_official_info,
    get_served_song_vids,
    record_served_song_vid,
    resolve_youtube_music_link,
)
from src.skills.music.templates import SONG_TEMPLATE
from src.skills.music import skill_music_discovery
from src.skills.youtube.catalogs import BARE_RANDOM_SONG_TERMS, KNOWN_BARE_MUSIC_GENRES
from src.skills.youtube.engine.client import search_youtube
from src.skills.youtube.templates import (
    VIDEO_RESULTS_TEMPLATE,
    build_no_tracks_found_message,
    build_no_videos_found_message,
)

MAX_YOUTUBE_RESULTS = 5


def skill_youtube_search(query: str, max_results: Optional[int] = 1, is_music: Optional[bool] = False) -> str:
    """
    Searches YouTube for videos to WATCH (dance clips, tutorials, live sets, visual performances) or,
    in music mode, fetches the playback link for a specific song found via `skill_music_discovery`.

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
            if (" by " not in query.lower() and " - " not in query.lower()) and (
                clean_q_low.lower() in BARE_RANDOM_SONG_TERMS or generic_m or clean_q_low.lower() in KNOWN_BARE_MUSIC_GENRES
            ):
                if clean_q_low.lower() in BARE_RANDOM_SONG_TERMS or (generic_m and generic_m.group(1).strip().lower() in BARE_RANDOM_SONG_TERMS):
                    print(f"🔄 [YouTube Search Skill]: Redirecting generic song request '{query}' -> skill_music_discovery().")
                    return skill_music_discovery()
                genre_term = generic_m.group(1).strip() if generic_m else clean_q_low
                print(f"🔄 [YouTube Search Skill]: Redirecting genre/vibe query '{query}' -> skill_music_discovery(genre='{genre_term}').")
                return skill_music_discovery(genre=genre_term)

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
                return build_no_tracks_found_message(reason_detail)

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
            return build_no_videos_found_message(query)

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

