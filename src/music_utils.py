"""
Backwards-compatibility re-export bridge for music utilities.
All core music parsing, metadata extraction, and Discord formatting logic
resides in `src.skills.music.utils`.
"""

from src.skills.music.utils import (
    MUSIC_METADATA_CACHE,
    format_song_card,
    get_music_metadata,
    is_placeholder_text,
    parse_song_meta,
    standardize_song_reply,
)

__all__ = [
    "MUSIC_METADATA_CACHE",
    "parse_song_meta",
    "get_music_metadata",
    "format_song_card",
    "is_placeholder_text",
    "standardize_song_reply",
]
