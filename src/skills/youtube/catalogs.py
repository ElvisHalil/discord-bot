"""
Static lookup registries, embed markers, and genre redirect sets for the YouTube domain skill.
"""

from __future__ import annotations

from typing import Tuple

UNPLAYABLE_EMBED_MARKERS: Tuple[str, ...] = (
    "Playback on other websites has been disabled",
    "blocked it from display on this website or application",
    '"playableInEmbed":false',
    '"playableInEmbed": false',
    "UNPLAYABLE",
    "LOGIN_REQUIRED",
    "Video unavailable",
    "This video is unavailable",
    "This video is private",
    "error-message",
)

BARE_RANDOM_SONG_TERMS: frozenset[str] = frozenset({
    "song", "songs", "track", "tracks", "music", "random", "random song",
    "random track", "good", "good song", "something", "anything", "",
})

KNOWN_BARE_MUSIC_GENRES: frozenset[str] = frozenset({
    "phonk", "drift phonk", "synthwave", "retrowave", "darksynth", "hardstyle",
    "dnb", "drum and bass", "drum & bass", "drumnbass", "jungle", "neurofunk",
    "techno", "hard techno", "melodic techno", "house", "tech house", "deep house",
    "trance", "psytrance", "dubstep", "edm", "electronic", "ambient", "lofi", "lo-fi",
    "rap", "hip hop", "hip-hop", "trap", "drill", "uk drill", "german rap", "deutschrap",
    "rock", "metal", "heavy metal", "indie", "pop", "r&b", "rnb", "jazz", "classical",
    "banger", "chill", "melodic", "hard", "dark", "sad", "love", "workout",
})

