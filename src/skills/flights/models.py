"""
Typed @dataclass(slots=True) domain models for the flights skill package.
"""

from dataclasses import dataclass, field
import time
from typing import Any, Dict, Iterator, Optional, Tuple, Union

from src.skills.flights.enums import (
    CabinClass,
    FlightState,
    SortPreference,
    TripType,
)
from src.utils.domain_vocab import DictLikeMixin


@dataclass(slots=True, frozen=True)
class AirportRecord:
    """Typed airport directory record supporting both attribute and tuple-unpacking access."""
    iata: str
    name: str
    city: str
    country: str

    def as_tuple(self) -> Tuple[str, str, str, str]:
        return (self.iata, self.name, self.city, self.country)

    def __iter__(self) -> Iterator[str]:
        yield self.iata
        yield self.name
        yield self.city
        yield self.country

    def __getitem__(self, index: Union[int, slice]) -> Any:
        return self.as_tuple()[index]

    def __len__(self) -> int:
        return 4

    def __eq__(self, other: object) -> bool:
        if isinstance(other, AirportRecord):
            return self.as_tuple() == other.as_tuple()
        if isinstance(other, tuple):
            return self.as_tuple() == other
        return False


@dataclass(slots=True, frozen=True)
class NearbyHubRecord:
    """Typed nearby major international hub alternative record."""
    iata: str
    city: str

    def as_tuple(self) -> Tuple[str, str]:
        return (self.iata, self.city)

    def __iter__(self) -> Iterator[str]:
        yield self.iata
        yield self.city

    def __getitem__(self, index: Union[int, slice]) -> Any:
        return self.as_tuple()[index]

    def __len__(self) -> int:
        return 2

    def __eq__(self, other: object) -> bool:
        if isinstance(other, NearbyHubRecord):
            return self.as_tuple() == other.as_tuple()
        if isinstance(other, tuple):
            return self.as_tuple() == other
        return False


@dataclass(slots=True, frozen=True)
class GatewayAirportRecord:
    """Typed international gateway airport record for 1-stop connecting schedule verification."""
    iata: str
    name: str
    city: str

    def as_tuple(self) -> Tuple[str, str, str]:
        return (self.iata, self.name, self.city)

    def __iter__(self) -> Iterator[str]:
        yield self.iata
        yield self.name
        yield self.city

    def __getitem__(self, index: Union[int, slice]) -> Any:
        return self.as_tuple()[index]

    def __len__(self) -> int:
        return 3

    def __eq__(self, other: object) -> bool:
        if isinstance(other, GatewayAirportRecord):
            return self.as_tuple() == other.as_tuple()
        if isinstance(other, tuple):
            return self.as_tuple() == other
        return False


@dataclass(slots=True)
class FlightSearchState(DictLikeMixin):
    """Per-channel conversational state for multi-turn flight queries."""
    origin: Optional[Union[AirportRecord, Tuple[str, str, str, str]]] = None
    destination: Optional[Union[AirportRecord, Tuple[str, str, str, str]]] = None
    dep_date: Optional[str] = None
    ret_date: Optional[str] = None
    dep_month_window: Optional[Tuple[str, str, str]] = None
    ret_month_window: Optional[Tuple[str, str, str]] = None
    trip_type: str = TripType.ONE_WAY
    max_stops: Optional[int] = None
    sort_preference: str = SortPreference.BEST
    trip_days: Optional[int] = None
    cabin: str = CabinClass.ECONOMY
    currency: str = "CAD"


@dataclass(slots=True)
class TrackedFlight(DictLikeMixin):
    """Represents a live commercial flight being monitored for status/gate/delay/landing alerts."""
    channel_id: int
    flight_number: str
    callsign: str
    airline: str
    origin_code: str
    dest_code: str
    last_state: str = FlightState.SCHEDULED
    last_status_text: str = "Scheduled"
    last_origin_gate: Optional[str] = None
    last_dest_gate: Optional[str] = None
    last_delay_mins: int = 0
    last_snapshot: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    last_polled_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class TrackedPriceWatch(DictLikeMixin):
    """Represents a route & date being monitored for Google Flights ticket price drops."""
    channel_id: int
    origin_code: str
    origin_city: str
    dest_code: str
    dest_city: str
    date: str
    return_date: Optional[str] = None
    cabin: str = CabinClass.ECONOMY
    currency: str = "CAD"
    baseline_price: int = 0
    lowest_seen_price: int = 0
    last_alerted_price: Optional[int] = None
    target_price: Optional[int] = None
    best_airline: str = ""
    booking_url: str = ""
    direct_only: bool = False
    created_at: float = field(default_factory=time.time)
    last_polled_at: float = field(default_factory=time.time)

    @property
    def watch_id(self) -> str:
        ret_part = f":{self.return_date}" if self.return_date else ""
        dir_part = ":direct" if self.direct_only else ""
        return f"{self.origin_code}-{self.dest_code}:{self.date}{ret_part}:{self.cabin}{dir_part}"
