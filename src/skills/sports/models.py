"""
Typed @dataclass(slots=True) domain models for the sports skill package.
"""

from dataclasses import dataclass, field
import time
from typing import Any, Iterator, List, Set, Tuple, Union

from src.skills.sports.enums import MatchState
from src.utils.domain_vocab import DictLikeMixin


@dataclass(slots=True)
class SoccerLeagueConfig(DictLikeMixin):
    """Typed configuration for a major soccer competition."""
    code: str
    name: str
    aliases: Tuple[str, ...]


@dataclass(slots=True)
class UniversalLeagueConfig(DictLikeMixin):
    """Typed configuration for an ESPN sport/league in the universal registry."""
    sport: str
    league: str
    label: str
    icon: str
    aliases: Tuple[str, ...]


@dataclass(slots=True, frozen=True)
class EsportsStreamChannel:
    """Typed YouTube esports broadcast channel record supporting attribute and tuple-unpacking access."""
    name: str
    handle: str
    channel_id: str

    def as_tuple(self) -> Tuple[str, str, str]:
        return (self.name, self.handle, self.channel_id)

    def __iter__(self) -> Iterator[str]:
        yield self.name
        yield self.handle
        yield self.channel_id

    def __getitem__(self, index: Union[int, slice]) -> Any:
        return self.as_tuple()[index]

    def __len__(self) -> int:
        return 3

    def __eq__(self, other: object) -> bool:
        if isinstance(other, EsportsStreamChannel):
            return self.as_tuple() == other.as_tuple()
        if isinstance(other, tuple):
            return self.as_tuple() == other
        return False


@dataclass(slots=True)
class MatchSnapshot(DictLikeMixin):
    """Normalized state of a live or upcoming sports event at a single point in time."""

    state: str = MatchState.PRE
    completed: bool = False
    status_name: str = ""  # e.g. 'STATUS_IN_PROGRESS', 'STATUS_HALFTIME', 'STATUS_FINAL'
    status_detail: str = "Scheduled"  # e.g. "33'", "HT", "Q2 04:12", "FT"
    period: int = 0
    home_team: str = "Home"
    away_team: str = "Away"
    home_score: str = "0"
    away_score: str = "0"
    notable_events: List[Tuple[str, str]] = field(default_factory=list)
    seen_event_ids: Set[str] = field(default_factory=set)
    stats_summary: str = ""

    @property
    def is_halftime(self) -> bool:
        det_upper = (self.status_detail or "").strip().upper()
        name_upper = (self.status_name or "").strip().upper()
        return (
            "HALFTIME" in name_upper
            or det_upper in ("HT", "HALFTIME", "HALF TIME", "HALF")
            or "HALFTIME" in det_upper
        )

    @property
    def is_period_end(self) -> bool:
        det_upper = (self.status_detail or "").strip().upper()
        name_upper = (self.status_name or "").strip().upper()
        return "END_PERIOD" in name_upper or det_upper.startswith("END OF ")

    @property
    def is_finished(self) -> bool:
        name_upper = (self.status_name or "").strip().upper()
        return (
            self.state == MatchState.POST
            or self.completed
            or name_upper in ("STATUS_FINAL", "STATUS_FULL_TIME", "STATUS_FINAL_AET", "STATUS_FINAL_PEN")
        )

    @property
    def is_aborted(self) -> bool:
        name_upper = (self.status_name or "").strip().upper()
        return any(k in name_upper for k in ("POSTPONED", "CANCELED", "CANCELLED", "ABANDONED", "SUSPENDED"))


@dataclass(slots=True)
class TrackedMatch(DictLikeMixin):
    """Represents a live or upcoming match being actively monitored for a Discord/Telegram channel."""

    channel_id: int
    sport_path: str
    league_code: str
    league_name: str
    event_id: str
    home_team: str
    away_team: str
    last_snapshot: MatchSnapshot
    created_at: float = field(default_factory=time.time)
    last_notified_score_diff: int = 0

