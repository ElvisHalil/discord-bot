"""
Domain StrEnums for the sports skill package.
"""

from enum import StrEnum


class MatchState(StrEnum):
    PRE = "pre"
    IN = "in"
    POST = "post"


class TrackerAction(StrEnum):
    START = "start"
    STOP = "stop"
    LIST = "list"


class SportId(StrEnum):
    SOCCER = "soccer"
    NHL = "nhl"
    NBA = "nba"
    F1 = "f1"
    CS = "cs"
    SPORTS = "sports"


class CsQueryType(StrEnum):
    ALL = "all"
    LIVE_STREAMS = "live_streams"
    TOURNAMENTS = "tournaments"
    RANKINGS = "rankings"
    EVENT_INFO = "event_info"
    PLAYER_STATS = "player_stats"
    ODDS = "odds"

