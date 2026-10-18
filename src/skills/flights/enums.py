"""
Domain StrEnums for the flights skill package.
"""

from enum import StrEnum


class FlightState(StrEnum):
    """Live commercial flight status states."""
    SCHEDULED = "scheduled"
    AIRBORNE = "airborne"
    LANDED = "landed"
    DELAYED = "delayed"
    CANCELLED = "cancelled"
    DIVERTED = "diverted"


class FlightTrackerAction(StrEnum):
    """Actions supported by the live flight & route price tracker."""
    TRACK = "track"
    START = "start"
    UNTRACK = "untrack"
    STOP = "stop"
    LIST = "list"
    CLEAR = "clear"


class CabinClass(StrEnum):
    """Supported airline cabin classes."""
    ECONOMY = "economy"
    PREMIUM_ECONOMY = "premium-economy"
    BUSINESS = "business"
    FIRST = "first"


class TripType(StrEnum):
    """Supported flight itinerary trip types."""
    ONE_WAY = "one-way"
    ROUND_TRIP = "round-trip"


class SortPreference(StrEnum):
    """Supported flight itinerary sorting preferences."""
    BEST = "best"
    CHEAPEST = "cheapest"
    SHORTEST = "shortest"
    FASTEST = "shortest"

