"""
Typed string enums for the YouTube domain skill.
"""

from __future__ import annotations

from enum import StrEnum


class YouTubeSearchMode(StrEnum):
    """Search mode for `skill_youtube_search`."""
    VIDEO = "video"
    MUSIC = "music"


class YouTubeProvider(StrEnum):
    """Upstream search providers for YouTube discovery."""
    DDG = "ddg"
    API_V3 = "api_v3"

