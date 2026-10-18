"""
Centralized prompt templates and LLM formatting instructions for the YouTube skill.
"""

from __future__ import annotations

VIDEO_RESULTS_TEMPLATE = (
    "=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
    "Present the video(s) to the user.\n"
    "1. Paste the raw standard YouTube video link (https://www.youtube.com/watch?v=...) on its own blank line so Discord embeds the media player.\n"
    "2. Do NOT wrap the URL in markdown `[Title](link)` or `<>` brackets, and do NOT use `music.youtube.com` in video mode.\n"
    "3. If searching for a single video, present ONLY the best single link."
)


def build_no_tracks_found_message(reason_detail: str) -> str:
    """Build the fallback LLM instruction when no embeddable YouTube Music link is found."""
    return (
        f"NO_TRACKS_FOUND: {reason_detail}\n"
        f"CRITICAL INSTRUCTIONS FOR YOUR RESPONSE:\n"
        f"1. If you were picking a song from a `skill_music_discovery` list, immediately call `skill_youtube_search(query='<Another Song Title> by <Artist Name>', is_music=True)` for a DIFFERENT track from that list.\n"
        f"2. Otherwise, do NOT output the '**Song:**' or '**Artist:**' template and do NOT output any URL or placeholder.\n"
        f"3. Simply inform the user in a short conversational sentence that the record label has disabled Discord playback for that track on YouTube (or that no working alternative upload exists)."
    )


def build_no_videos_found_message(query: str) -> str:
    """Build the fallback LLM instruction when no YouTube video is found."""
    return (
        f"NO_VIDEOS_FOUND: Could not find any playable YouTube video for '{query}'.\n"
        f"CRITICAL INSTRUCTIONS FOR YOUR RESPONSE:\n"
        f"1. Do NOT guess or invent any YouTube link or URL placeholder.\n"
        f"2. Simply tell the user in a short sentence that no matching video was found."
    )

