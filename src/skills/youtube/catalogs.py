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

# Official sports broadcasters, gaming publishers, and media channels that frequently disable
# third-party iframe embedding on Discord while remaining the authoritative source for video queries.
TRUSTED_VIDEO_CHANNELS: frozenset[str] = frozenset({
    "formula 1", "f1", "nfl", "nba", "nhl", "mlb", "ufc", "premier league",
    "sky sports", "sky sports f1", "sky sports football", "espn", "cbs sports",
    "cbs sports golazo", "nbc sports", "tnt sports", "dazn", "uefa", "fifa",
    "laliga", "serie a", "bundesliga", "ligue 1", "sportsnet", "tsn",
    "motogp", "wrc", "indycar", "nascar", "red bull", "red bull motorsports",
    "esl", "esl counter-strike", "blast premier", "pgl", "valorant champions tour",
    "lolesports", "riot games", "valve", "ea sports", "playstation", "xbox",
    "nintendo", "rockstar games", "ign", "gamespot", "the game awards",
    "marvel entertainment", "star wars", "dc", "netflix", "prime video",
    "hbo", "max", "apple", "apple tv", "sony pictures entertainment",
    "warner bros. pictures", "universal pictures", "paramount pictures",
    "a24", "bbc", "bbc sport", "itvx", "channel 4",
    "federal reserve", "bank of canada", "european central bank",
})

# Shorthand query tokens mapped to their official YouTube channel names (lowercase)
CHANNEL_QUERY_ALIASES: dict[str, str] = {
    "f1": "formula 1",
    "formula1": "formula 1",
    "grand prix": "formula 1",
    "fp1": "formula 1",
    "fp2": "formula 1",
    "fp3": "formula 1",
    "epl": "premier league",
    "ucl": "cbs sports golazo",
    "champions league": "cbs sports golazo",
    "cs2": "esl counter-strike",
    "fomc": "federal reserve",
    "boc": "bank of canada",
}


