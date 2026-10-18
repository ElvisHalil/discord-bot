"""
Domain StrEnums for the music skill package.
"""

from enum import StrEnum


class TempoLevel(StrEnum):
    """3-step tempo axis on the Music Spectrum grid."""
    SLOW = "slow"
    MID = "mid"
    FAST = "fast"


class IntensityLevel(StrEnum):
    """3-step intensity/hardness axis on the Music Spectrum grid."""
    SOFT = "soft"
    MID = "mid"
    HARD = "hard"


class SpectrumZoneId(StrEnum):
    """Canonical identifiers for the 9 cells of the 3x3 Tempo x Intensity Music Spectrum."""
    SLOW_SOFT = "slow_soft"
    MID_SOFT = "mid_soft"
    FAST_SOFT = "fast_soft"
    SLOW_MID = "slow_mid"
    MID_MID = "mid_mid"
    FAST_MID = "fast_mid"
    SLOW_HARD = "slow_hard"
    MID_HARD = "mid_hard"
    FAST_HARD = "fast_hard"


class MoodCategory(StrEnum):
    """Canonical mood/vibe categories for music discovery."""
    BANGER = "banger"
    WORKOUT = "workout"
    MELODIC = "melodic"
    HARD = "hard"
    DARK = "dark"
    CHILL = "chill"
    SAD = "sad"
    LOVE = "love"


class CanonicalGenre(StrEnum):
    """Canonical parent genres used in the Genre x Mood matrix."""
    DNB = "dnb"
    TECHNO = "techno"
    HOUSE = "house"
    TRANCE = "trance"
    DUBSTEP = "dubstep"
    ELECTRONIC = "electronic"
    RAP = "rap"
    RNB = "rnb"
    ROCK = "rock"
    METAL = "metal"
    POP = "pop"
    COUNTRY = "country"


class MusicProvider(StrEnum):
    """Identifiers for music chart and similarity aggregators."""
    LASTFM = "lastfm"
    DEEZER = "deezer"
    FREQBLOG = "freqblog"
    BEATPORT = "beatport"
    CHARTS = "charts"
    YOUTUBE = "youtube"


class AudioAction(StrEnum):
    """Supported actions for `skill_music_analysis`."""
    BPM_KEY = "bpm_key"
    MATCH_MOOD = "match_mood"
    TRANSITION = "transition"
    BPM_SEARCH = "bpm_search"
    HARMONIC_RADIO = "harmonic_radio"


