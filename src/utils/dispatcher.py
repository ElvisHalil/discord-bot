"""
Shared command and reaction dispatching utilities used by both the Discord gateway (main.py)
and the Telegram bridge (src/utils/telegram.py).

Keeps song-card metadata extraction, 👍 reaction recommendation resolution, and
direct tracker commands (!trackflight, !untrackflight, !trackedflights, !track, !untrack, !tracked)
in a single place without coupling `src/utils/` upward to `src/skills/`.
"""

from __future__ import annotations

import random
import re
from typing import Any, Callable, Dict, Optional

from src.context import set_active_channel_id
from src.utils.workers import run_in_worker


def extract_song_card_target(card_text: str) -> Optional[Dict[str, str]]:
    """
    Parse a bot song card message and extract the cleaned song title, artist, and search query.
    Returns None if the message is not a valid YouTube Music song card.
    """
    if not card_text or "music.youtube.com/" not in card_text:
        return None

    m_card_song = re.search(r"(?im)^\s*(?:\*\*)?(?:Song|Track):\*{0,2}\s*(.+)", card_text)
    m_card_art = re.search(r"(?im)^\s*(?:\*\*)?Artist:\*{0,2}\s*(.+)", card_text)
    if not (m_card_song and m_card_art):
        return None

    video_match = re.search(r"music\.youtube\.com\/watch\?v=([0-9A-Za-z_-]{11})", card_text)
    if not video_match:
        return None

    orig_title = m_card_song.group(1).strip()
    orig_artist = m_card_art.group(1).strip()

    clean_title = re.sub(
        r"[\(\[\{].*?(?:official|video|audio|lyrics|visualizer|download|remastered|hd|4k|hq|mix).*?[\)\]\}]",
        "",
        orig_title,
        flags=re.IGNORECASE,
    )
    clean_title = re.sub(r"[\(\[\{](?:feat\.|ft\.).*?[\)\]\}]", "", clean_title, flags=re.IGNORECASE)
    if " - " in clean_title:
        parts = clean_title.split(" - ", 1)
        orig_artist = parts[0].strip()
        clean_title = parts[1].strip()
    clean_title = re.sub(r"[\(\[\{].*?[\)\]\}]", "", clean_title).strip()
    clean_title = re.sub(r"[-–—|/].*$", "", clean_title).strip() or orig_title

    search_query = f"{clean_title} by {orig_artist}".strip() if orig_artist else clean_title
    orig_display = f"'{orig_title}' by '{orig_artist}'" if orig_artist else f"'{orig_title}'"

    return {
        "orig_title": orig_title,
        "orig_artist": orig_artist,
        "clean_title": clean_title,
        "search_query": search_query,
        "orig_display": orig_display,
        "video_id": video_match.group(1),
    }


async def resolve_reaction_recommendation(
    channel_id: int,
    card_target: Dict[str, str],
    music_discovery_fn: Callable[..., str],
    youtube_search_fn: Callable[..., str],
    allow_chart_fallback: bool = True,
    max_candidates: int = 6,
) -> Dict[str, Any]:
    """
    Resolve a verified YouTube Music recommendation for a 👍 reaction on a song card.
    Accepts `music_discovery_fn` and `youtube_search_fn` from the caller module so
    unit-test mocks on `main.tools.*` or `src.utils.telegram.tools.*` intercept cleanly.
    """
    clean_title = card_target["clean_title"]
    search_query = card_target["search_query"]

    set_active_channel_id(channel_id)
    similar_res = await run_in_worker(music_discovery_fn, similar_to=search_query)
    is_unrelated_fallback = "NO DIRECT SIMILAR SONGS FOUND" in similar_res

    similar_tracks = []
    for line in similar_res.split("\n"):
        line_s = line.strip()
        if line_s.startswith("- "):
            track_name = line_s[2:].strip()
            if clean_title.lower() not in track_name.lower():
                similar_tracks.append(track_name)

    if not similar_tracks and allow_chart_fallback and "=== VERIFIED YOUTUBE MUSIC RESULT" not in similar_res:
        is_unrelated_fallback = True
        print("⚠️ [Reaction Fallback]: No similar tracks found, pulling general charts...")
        bp_res = await run_in_worker(music_discovery_fn, genre=None)
        similar_res = bp_res
        for line in bp_res.split("\n"):
            line_s = line.strip()
            if line_s.startswith("- "):
                similar_tracks.append(line_s[2:].strip())

    verified_url: Optional[str] = None
    chosen_track: Optional[str] = None
    if "=== VERIFIED YOUTUBE MUSIC RESULT" in similar_res:
        pre_block = similar_res.split("=== VERIFIED YOUTUBE MUSIC RESULT", 1)[1]
        m_pre_song = re.search(r"(?m)^Song:\s*(.+)$", pre_block)
        m_pre_art = re.search(r"(?m)^Artist:\s*(.+)$", pre_block)
        m_pre_url = re.search(
            r"(?m)^Link:\s*(https:\/\/(?:music|www)\.youtube\.com\/watch\?v=[0-9A-Za-z_-]{11})",
            pre_block,
        )
        if m_pre_url and m_pre_song and m_pre_art:
            cand_song = m_pre_song.group(1).strip()
            cand_art = m_pre_art.group(1).strip()
            if clean_title.lower() not in cand_song.lower():
                verified_url = m_pre_url.group(1).replace("www.youtube.com", "music.youtube.com")
                chosen_track = f"{cand_song} by {cand_art}"

    if not verified_url or not chosen_track:
        random.shuffle(similar_tracks)
        for candidate in similar_tracks[:max_candidates]:
            yt_output = await run_in_worker(youtube_search_fn, candidate, max_results=1, is_music=True)
            m_url = re.search(r"(https:\/\/(?:music|www)\.youtube\.com\/watch\?v=[0-9A-Za-z_-]{11})", yt_output)
            if m_url:
                verified_url = m_url.group(1).replace("www.youtube.com", "music.youtube.com")
                chosen_track = candidate
                break

    vid: Optional[str] = None
    if verified_url:
        m_vid = re.search(r"watch\?v=([0-9A-Za-z_-]{11})", verified_url)
        vid = m_vid.group(1) if m_vid else None

    return {
        "verified_url": verified_url,
        "chosen_track": chosen_track,
        "vid": vid,
        "is_unrelated_fallback": is_unrelated_fallback,
    }


async def dispatch_tracker_command(
    text: str,
    channel_id: int,
    flight_tracker_fn: Callable[..., str],
    flight_tracker_service: Any,
    sports_tracker_fn: Callable[..., str],
    sports_tracker_service: Any,
) -> Optional[str]:
    """
    Handle direct flight and sports tracker bang commands (`!trackflight`, `!untrackflight`,
    `!trackedflights`, `!track`, `!untrack`, `!tracked`).
    Returns the formatted response string if `text` matched a tracker command, or None otherwise.
    """
    if not text:
        return None
    low = text.lower()

    if low.startswith("!trackflight "):
        target_q = text[13:].strip()
        set_active_channel_id(channel_id)
        res = await run_in_worker(flight_tracker_fn, action="track", query=target_q)
        flight_tracker_service.ensure_loop_running()
        return res

    if low.startswith("!untrackflight"):
        target_q = text[14:].strip()
        set_active_channel_id(channel_id)
        return await run_in_worker(flight_tracker_fn, action="untrack", query=target_q)

    if low in ("!trackedflights", "!trackflight"):
        set_active_channel_id(channel_id)
        return await run_in_worker(flight_tracker_fn, action="list")

    if low.startswith("!track "):
        target_q = text[7:].strip()
        set_active_channel_id(channel_id)
        res = await run_in_worker(sports_tracker_fn, action="start", query=target_q)
        sports_tracker_service.ensure_loop_running()
        return res

    if low.startswith("!untrack"):
        target_q = text[8:].strip()
        set_active_channel_id(channel_id)
        return await run_in_worker(sports_tracker_fn, action="stop", query=target_q)

    if low in ("!tracked", "!track"):
        set_active_channel_id(channel_id)
        return await run_in_worker(sports_tracker_fn, action="list")

    return None

