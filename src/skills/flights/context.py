"""
Per-channel flight state tracking, tool argument enrichment, and turn-0 missed-tool-call recovery.
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple, Union

from src.context import get_active_channel_id
from src.skills.flights.catalogs import MONTH_MAP as _MONTH_MAP
from src.skills.flights.enums import FlightTrackerAction
from src.skills.flights.models import AirportRecord, FlightSearchState

_CHANNEL_FLIGHT_STATE: Dict[int, FlightSearchState] = {}
_CHANNEL_LAST_ROUTE: Dict[int, Tuple[Union[AirportRecord, Tuple[str, str, str, str]], Union[AirportRecord, Tuple[str, str, str, str]]]] = {}
_CHANNEL_LAST_WINDOW: Dict[int, dict] = {}


def _resolve_cid(channel_id: Optional[int] = None) -> int:
    return channel_id if channel_id is not None else (get_active_channel_id() or 0)


def get_channel_flight_state(channel_id: Optional[int] = None) -> Optional[FlightSearchState]:
    return _CHANNEL_FLIGHT_STATE.get(_resolve_cid(channel_id))


def update_channel_flight_state(state: FlightSearchState, channel_id: Optional[int] = None) -> None:
    cid = _resolve_cid(channel_id)
    _CHANNEL_FLIGHT_STATE[cid] = state
    if state.origin and state.destination:
        _CHANNEL_LAST_ROUTE[cid] = (state.origin, state.destination)


def clear_channel_flight_state(channel_id: Optional[int] = None) -> None:
    if channel_id is None:
        _CHANNEL_FLIGHT_STATE.clear()
        _CHANNEL_LAST_ROUTE.clear()
        _CHANNEL_LAST_WINDOW.clear()
        return
    cid = _resolve_cid(channel_id)
    _CHANNEL_FLIGHT_STATE.pop(cid, None)
    _CHANNEL_LAST_ROUTE.pop(cid, None)
    _CHANNEL_LAST_WINDOW.pop(cid, None)


def record_channel_last_route(
    origin_info: Union[AirportRecord, Tuple[str, str, str, str]],
    dest_info: Union[AirportRecord, Tuple[str, str, str, str]],
    channel_id: Optional[int] = None,
) -> None:
    cid = _resolve_cid(channel_id)
    _CHANNEL_LAST_ROUTE[cid] = (origin_info, dest_info)
    st = _CHANNEL_FLIGHT_STATE.get(cid)
    if st:
        st.origin = origin_info
        st.destination = dest_info
    else:
        _CHANNEL_FLIGHT_STATE[cid] = FlightSearchState(origin=origin_info, destination=dest_info)


def get_channel_last_route(
    channel_id: Optional[int] = None,
) -> Optional[Tuple[Union[AirportRecord, Tuple[str, str, str, str]], Union[AirportRecord, Tuple[str, str, str, str]]]]:
    cid = _resolve_cid(channel_id)
    st = _CHANNEL_FLIGHT_STATE.get(cid)
    if st and st.origin and st.destination:
        return (st.origin, st.destination)
    return _CHANNEL_LAST_ROUTE.get(cid)


def record_channel_last_window(
    window_data: dict,
    channel_id: Optional[int] = None,
) -> None:
    cid = _resolve_cid(channel_id)
    _CHANNEL_LAST_WINDOW[cid] = dict(window_data)


def get_channel_last_window(
    channel_id: Optional[int] = None,
) -> Optional[dict]:
    cid = _resolve_cid(channel_id)
    return _CHANNEL_LAST_WINDOW.get(cid)


def detect_missed_flight_tool_call(
    history: list,
    active_channel_id: Optional[int] = None,
) -> Optional[Tuple[str, dict]]:
    """
    Detects when an 8B model attempts to answer a flight search or follow-up refinement
    from stale chat history without calling `skill_flight_info` or `skill_flight_tracker`.
    """
    if not history:
        return None

    latest_user_text = ""
    for msg in reversed(history):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        if role == "user":
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
            latest_user_text = str(content or "").strip()
            latest_user_text = re.sub(r"^\[[^\]]+\]:\s*", "", latest_user_text).strip()
            break
    if not latest_user_text:
        return None

    q_low = latest_user_text.lower()
    # Ignore explicit bot commands
    if q_low.startswith("!"):
        return None

    # 1. Explicit flight tracking intent
    if re.search(r"\b(?:track|watch|alert|untrack|stop\s+tracking)\b", q_low) and re.search(
        r"\b(?:flights?|airfare|prices?|fares?)\b", q_low
    ):
        return ("skill_flight_tracker", {"action": FlightTrackerAction.TRACK, "query": latest_user_text})

    month_pattern = "|".join(_MONTH_MAP.keys())
    has_flight_kw = bool(
        re.search(
            r"\b(?:flights?|fly|flying|airfare|airlines?|nonstop|non-stop|layovers?|round[\s\-]?trip|one[\s\-]?way|1[\s\-]?stop)\b",
            q_low,
        )
    )
    has_refinement_cue = bool(
        re.search(
            rf"\b(?:return(?:ing)?|back\s+in|leav(?:e|ing)|depart(?:ing)?|for\s+\d+\s*(?:days?|weeks?|months?)|"
            rf"1[\s\-]?stops?|one[\s\-]?stops?|single[\s\-]?stops?|nonstop|direct|shortest|fastest|cheapest|"
            rf"round[\s\-]?trip|({month_pattern}))\b",
            q_low,
        )
    )

    state = get_channel_flight_state(active_channel_id)
    had_prior_flight_tool = any(
        (m.get("name") if isinstance(m, dict) else getattr(m, "name", None)) in ("skill_flight_info", "skill_flight_tracker")
        for m in history
    )

    if has_flight_kw and (has_refinement_cue or " to " in q_low or " from " in q_low or state or had_prior_flight_tool):
        return ("skill_flight_info", {"query": latest_user_text})

    if (state or had_prior_flight_tool) and has_refinement_cue:
        return ("skill_flight_info", {"query": latest_user_text})

    return None

