import calendar
from datetime import datetime, timedelta
import logging
import re
from typing import Optional, Tuple

from src.skills.flights.catalogs import (
    AIRLINE_CODES,
    AIRPORT_DIRECTORY,
    MONTH_MAP as _MONTH_MAP,
    NEARBY_HUB_AIRPORTS,
    NON_FLIGHT_NUMBER_PREFIXES,
    NON_LOCATION_STOPWORDS as _NON_LOCATION_WORDS,
    ORIGIN_INTERNATIONAL_GATEWAYS,
    WEEKDAY_MAP as _WEEKDAY_MAP,
)
from src.skills.flights.context import (
    clear_channel_flight_state,
    detect_missed_flight_tool_call,
    get_channel_flight_state,
    get_channel_last_route,
    get_channel_last_window,
    record_channel_last_route,
    record_channel_last_window,
    update_channel_flight_state,
)
from src.skills.flights.enums import (
    CabinClass,
    FlightTrackerAction,
    SortPreference,
    TripType,
)
from src.skills.flights.models import (
    AirportRecord,
    FlightSearchState,
    GatewayAirportRecord,
    NearbyHubRecord,
)
from src.utils.cache import BoundedCache
from src.utils.timezones import get_local_now

logger = logging.getLogger(__name__)

# Bounded in-memory cache for Flightradar24 dynamic airport lookups
_AIRPORT_RESOLVE_CACHE: dict[str, Optional[AirportRecord]] = BoundedCache(maxsize=512)


def resolve_airport(location: Optional[str]) -> Optional[AirportRecord]:
    """
    Resolves a city name, airport name, or 3-letter IATA code into an
    AirportRecord(iata, name, city, country).
    Falls back to cached Flightradar24 search for unlisted regional airports.
    """
    if not location:
        return None
    cleaned = re.sub(
        r"\b(?:airport|international|intl|flights?|tickets?|cheap|cheapest|direct|nonstop|one\s*way|round\s*trip)\b",
        "",
        location,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"[^a-zA-Z\s.\-']", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    if not cleaned or cleaned in _NON_LOCATION_WORDS:
        return None
    cleaned_words = cleaned.split()
    if all(w in _NON_LOCATION_WORDS for w in cleaned_words):
        return None

    # 1. Exact lookup in fast directory or dynamic cache
    if cleaned in AIRPORT_DIRECTORY:
        return AIRPORT_DIRECTORY[cleaned]
    if cleaned in _AIRPORT_RESOLVE_CACHE:
        return _AIRPORT_RESOLVE_CACHE[cleaned]

    # 2. Check if a 3-letter IATA code is inside parentheses or standalone
    m_iata = re.search(r"\b([A-Za-z]{3})\b", location.strip())
    if m_iata:
        cand = m_iata.group(1).lower()
        if cand in AIRPORT_DIRECTORY:
            return AIRPORT_DIRECTORY[cand]
        if cand in _AIRPORT_RESOLVE_CACHE and _AIRPORT_RESOLVE_CACHE[cand] is not None:
            return _AIRPORT_RESOLVE_CACHE[cand]

    # 3. Substring match against multi-word city keys (longest first)
    for key in sorted(AIRPORT_DIRECTORY.keys(), key=len, reverse=True):
        if len(key) >= 4 and re.search(rf"\b{re.escape(key)}\b", cleaned):
            return AIRPORT_DIRECTORY[key]

    # 4. If it's an exact 3-letter uppercase code not in our static dict, accept it (never lowercase English words)
    if re.fullmatch(r"[a-zA-Z]{3}", cleaned) and cleaned not in _NON_LOCATION_WORDS and location.strip().isupper():
        code_up = cleaned.upper()
        info = AirportRecord(code_up, f"{code_up} Airport", code_up, "")
        _AIRPORT_RESOLVE_CACHE[cleaned] = info
        return info

    # 5. Live fallback via Flightradar24 search (only for plausible 1-to-3-word location names)
    if len(cleaned_words) <= 3 and not any(w in _NON_LOCATION_WORDS for w in cleaned_words):
        try:
            from FlightRadarAPI import FlightRadar24API
            fr = FlightRadar24API()
            res = fr.search(cleaned, limit=5)
            airports = (res or {}).get("airport", [])
            for ap in airports:
                detail = ap.get("detail", {}) or {}
                iata = detail.get("iata") or ap.get("id")
                if iata and len(str(iata)) == 3:
                    iata_up = str(iata).upper()
                    label = ap.get("label") or f"{iata_up} Airport"
                    city_name = label.split("(")[0].strip()
                    resolved = AirportRecord(iata_up, label, city_name, "")
                    _AIRPORT_RESOLVE_CACHE[cleaned] = resolved
                    _AIRPORT_RESOLVE_CACHE[iata_up.lower()] = resolved
                    return resolved
        except Exception as e:
            logger.debug(f"FlightRadar24 airport search fallback failed for '{cleaned}': {e}")

    _AIRPORT_RESOLVE_CACHE[cleaned] = None
    return None


def extract_flight_numbers(text: str) -> list[str]:
    """
    Extracts commercial flight numbers (e.g. 'AC850', 'WS 102', 'UA123', 'ACA850')
    from natural language text without matching prices, durations ('for 3 weeks'), or dates.
    """
    if not text:
        return []
    found: list[str] = []
    for m in re.finditer(r"\b([A-Za-z]{2,3}|[A-Za-z]\d|\d[A-Za-z])[\s\-]?(\d{1,4})\b", text):
        raw_prefix = m.group(1)
        prefix = raw_prefix.upper()
        digits = m.group(2)
        if prefix.lower() in NON_FLIGHT_NUMBER_PREFIXES:
            continue
        trailing = text[m.end() :]
        if re.match(
            r"^\s*(?:weeks?|days?|months?|years?|stops?|layovers?|hours?|hrs?|mins?|minutes?|people|adults?|passengers?|tickets?|bags?|legs?)\b",
            trailing,
            re.IGNORECASE,
        ):
            continue
        has_flight_word = bool(re.search(r"\b(?:flight|track|status|radar|callsign|plane)\b", text, re.IGNORECASE))
        if prefix in AIRLINE_CODES or (has_flight_word and raw_prefix.isupper() and len(prefix) in (2, 3) and len(digits) >= 2):
            code = f"{prefix}{digits}"
            if code not in found:
                found.append(code)
    return found


def parse_natural_date(date_text: Optional[str], reference_dt: Optional[datetime] = None) -> Optional[str]:
    """
    Converts natural language date expressions ('tomorrow', 'next friday', 'nov 15',
    'May 20, 2027', '2026-11-15', 'in 2 weeks') into 'YYYY-MM-DD'.
    """
    if not date_text:
        return None
    now = reference_dt or get_local_now()
    txt = date_text.strip().lower()

    # 1. Explicit ISO YYYY-MM-DD (validated against calendar)
    m_iso = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", txt)
    if m_iso:
        y, mo, d = int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            pass

    # 2. Relative keywords
    if "day after tomorrow" in txt:
        return (now + timedelta(days=2)).strftime("%Y-%m-%d")
    if "tomorrow" in txt:
        return (now + timedelta(days=1)).strftime("%Y-%m-%d")
    if "today" in txt or "tonight" in txt:
        return now.strftime("%Y-%m-%d")

    m_in_days = re.search(r"\bin\s+(\d+)\s+(day|days|week|weeks)\b", txt)
    if m_in_days:
        num = int(m_in_days.group(1))
        unit = m_in_days.group(2)
        days = num * 7 if "week" in unit else num
        return (now + timedelta(days=days)).strftime("%Y-%m-%d")

    if "next month" in txt:
        return (now + timedelta(days=30)).strftime("%Y-%m-%d")
    if "next week" in txt:
        return (now + timedelta(days=7)).strftime("%Y-%m-%d")
    if "christmas" in txt:
        yr = now.year if (now.month, now.day) <= (12, 25) else now.year + 1
        return f"{yr:04d}-12-25"

    # 3. Month + Day (e.g. "Nov 15", "May 20, 2027", "15 November")
    month_pattern = "|".join(_MONTH_MAP.keys())
    m_md = re.search(rf"\b({month_pattern})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s*(20\d{{2}}))?\b", txt)
    if m_md:
        mo = _MONTH_MAP[m_md.group(1)]
        d = int(m_md.group(2))
        yr = int(m_md.group(3)) if m_md.group(3) else now.year
        try:
            cand = datetime(yr, mo, d)
            if not m_md.group(3) and cand.date() < now.date():
                cand = datetime(yr + 1, mo, d)
            return cand.strftime("%Y-%m-%d")
        except ValueError:
            pass

    m_dm = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({month_pattern})\.?(?:,?\s*(20\d{{2}}))?\b", txt)
    if m_dm:
        d = int(m_dm.group(1))
        mo = _MONTH_MAP[m_dm.group(2)]
        yr = int(m_dm.group(3)) if m_dm.group(3) else now.year
        try:
            cand = datetime(yr, mo, d)
            if not m_dm.group(3) and cand.date() < now.date():
                cand = datetime(yr + 1, mo, d)
            return cand.strftime("%Y-%m-%d")
        except ValueError:
            pass

    # 4. Weekday name ("this friday", "next monday", "friday")
    for w_name, w_idx in _WEEKDAY_MAP.items():
        m_w = re.search(rf"\b(next\s+)?{w_name}\b", txt)
        if m_w:
            days_ahead = (w_idx - now.weekday()) % 7
            if days_ahead == 0 or m_w.group(1):
                days_ahead += 7 if days_ahead == 0 else 0
            return (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    return None


def _extract_all_dates_from_text(text: str, now: datetime) -> list[str]:
    """Extracts up to 2 chronological dates from natural text (handling 'May 20, 2027 with return trip August 27, 2027' or 'Nov 15 to Nov 22')."""
    if not text:
        return []
    found: list[Tuple[int, str]] = []
    month_pattern = "|".join(_MONTH_MAP.keys())

    # 1. Explicit ISO dates (validated against calendar)
    for m in re.finditer(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            found.append((m.start(), datetime(y, mo, d).strftime("%Y-%m-%d")))
        except ValueError:
            pass

    # 2. Month + Day (+ optional Year) or Month range ("Nov 15 to 22")
    m_same_month_range = re.search(
        rf"\b({month_pattern})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:to|until|through|-)\s*(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s*(20\d{{2}}))?\b",
        text,
        re.IGNORECASE,
    )
    if m_same_month_range:
        mo = _MONTH_MAP[m_same_month_range.group(1).lower()]
        d1 = int(m_same_month_range.group(2))
        d2 = int(m_same_month_range.group(3))
        yr = int(m_same_month_range.group(4)) if m_same_month_range.group(4) else now.year
        try:
            c1 = datetime(yr, mo, d1)
            if not m_same_month_range.group(4) and c1.date() < now.date():
                yr += 1
                c1 = datetime(yr, mo, d1)
            c2 = datetime(yr, mo, d2)
            return [c1.strftime("%Y-%m-%d"), c2.strftime("%Y-%m-%d")]
        except ValueError:
            pass

    for m in re.finditer(rf"\b({month_pattern})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s*(20\d{{2}}))?\b", text, re.IGNORECASE):
        mo = _MONTH_MAP[m.group(1).lower()]
        d = int(m.group(2))
        yr = int(m.group(3)) if m.group(3) else now.year
        try:
            cand = datetime(yr, mo, d)
            if not m.group(3) and cand.date() < now.date():
                cand = datetime(yr + 1, mo, d)
            iso_s = cand.strftime("%Y-%m-%d")
            if all(iso_s != existing for _, existing in found):
                found.append((m.start(), iso_s))
        except ValueError:
            pass

    for m in re.finditer(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({month_pattern})\.?(?:,?\s*(20\d{{2}}))?\b", text, re.IGNORECASE):
        d = int(m.group(1))
        mo = _MONTH_MAP[m.group(2).lower()]
        yr = int(m.group(3)) if m.group(3) else now.year
        try:
            cand = datetime(yr, mo, d)
            if not m.group(3) and cand.date() < now.date():
                cand = datetime(yr + 1, mo, d)
            iso_s = cand.strftime("%Y-%m-%d")
            if all(iso_s != existing for _, existing in found):
                found.append((m.start(), iso_s))
        except ValueError:
            pass

    found.sort(key=lambda x: x[0])
    return [dt_s for _, dt_s in found]


def _month_window_for(yr: int, mo: int, now: datetime) -> Tuple[str, str, str]:
    last_d = calendar.monthrange(yr, mo)[1]
    start_d = max(1, (now + timedelta(days=1)).day) if (yr == now.year and mo == now.month) else 1
    end_d = min(last_d, start_d + 20)
    return (
        f"{yr:04d}-{mo:02d}-{start_d:02d}",
        f"{yr:04d}-{mo:02d}-{end_d:02d}",
        f"{calendar.month_name[mo]} {yr}",
    )


def _representative_tuesday(yr: int, mo: int, now: datetime) -> str:
    """Picks a representative mid-week Tuesday in (yr, mo) so 5x-weekly transatlantic routes are hit."""
    last_d = calendar.monthrange(yr, mo)[1]
    min_d = max(1, (now + timedelta(days=1)).day) if (yr == now.year and mo == now.month) else 4
    for d in range(min(min_d, last_d), last_d + 1):
        if datetime(yr, mo, d).weekday() == 1:
            return f"{yr:04d}-{mo:02d}-{d:02d}"
    return f"{yr:04d}-{mo:02d}-{min(min_d, last_d):02d}"


def parse_route_from_query(
    query: str,
    origin: Optional[str] = None,
    destination: Optional[str] = None,
    date: Optional[str] = None,
    return_date: Optional[str] = None,
    max_stops_arg: Optional[int] = None,
    sort_by_arg: Optional[str] = None,
    trip_type_arg: Optional[str] = None,
) -> dict:
    """
    Extracts origin, destination, departure date/month, return date/month, trip type,
    stop constraint, sort preference, cabin class, and currency from a natural-language query
    combined with explicit tool arguments and the channel's conversational FlightSearchState.
    """
    now = get_local_now()
    q_text = (query or "").strip()
    q_low = q_text.lower()
    prev_state = get_channel_flight_state()

    orig_from_query = None
    dest_from_query = None
    month_pattern = "|".join(_MONTH_MAP.keys())

    if q_text:
        # Remove month-to-month date ranges (e.g. "May to August next year") before matching "<A> to <B>" routes
        route_text = re.sub(
            rf"\b(?:from\s+)?({month_pattern})\.?\s+(?:to|through|until|thru|-|–|—)\s+({month_pattern})\.?(?:\s+(?:next|this)\s+year|\s+20\d{{2}})?\b",
            " ",
            q_text,
            flags=re.IGNORECASE,
        )
        route_terminator = r"(?:[?.!,;]*(?:\s+(?:on|in|for|with|via|this|next|tomorrow|today|leaving|departing|returning|round|one\b|under|below|\$|\d|dec|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov)|$))"

        # Pattern 1: "from <A> to <B>"
        m_from_to = re.search(
            rf"\bfrom\s+([A-Za-z\s\-']+?)\s+to\s+([A-Za-z\s\-']+?){route_terminator}",
            route_text,
            re.IGNORECASE,
        )
        if m_from_to:
            c1 = resolve_airport(m_from_to.group(1))
            c2 = resolve_airport(m_from_to.group(2))
            if c1 and c2 and c1[0] != c2[0]:
                orig_from_query = c1
                dest_from_query = c2

        # Pattern 2: "to <B> from <A>"
        if not (orig_from_query and dest_from_query):
            m_to_from = re.search(
                rf"\bto\s+([A-Za-z\s\-']+?)\s+from\s+([A-Za-z\s\-']+?){route_terminator}",
                route_text,
                re.IGNORECASE,
            )
            if m_to_from:
                c1 = resolve_airport(m_to_from.group(1))
                c2 = resolve_airport(m_to_from.group(2))
                if c1 and c2 and c1[0] != c2[0]:
                    dest_from_query = c1
                    orig_from_query = c2

        # Pattern 3: "<A> to <B>" or "<A> - <B>" or "<A> -> <B>"
        if not (orig_from_query and dest_from_query):
            m_pair = re.search(
                rf"(?:flights?|tickets?|fly|flying|prices?|deals?)?\s*([A-Za-z\s\-']{{3,25}}?)\s+(?:to|->|—|-)\s+([A-Za-z\s\-']{{3,25}}?){route_terminator}",
                route_text,
                re.IGNORECASE,
            )
            if m_pair:
                c1_str = re.sub(
                    r"^(?:cheap|cheapest|best|shortest|fastest|direct|nonstop|find|search|check|show|get|give|me|for|from|flight|flights)\s+",
                    "",
                    m_pair.group(1).strip(),
                    flags=re.IGNORECASE,
                )
                c2_str = m_pair.group(2).strip()
                c1 = resolve_airport(c1_str)
                c2 = resolve_airport(c2_str)
                if c1 and c2 and c1[0] != c2[0]:
                    orig_from_query = c1
                    dest_from_query = c2

        # Pattern 4: Scan for any two distinct airports/cities in order of appearance in query
        if not (orig_from_query and dest_from_query):
            matches = []
            for key in sorted(AIRPORT_DIRECTORY.keys(), key=len, reverse=True):
                for m in re.finditer(rf"\b{re.escape(key)}\b", route_text.lower()):
                    info = AIRPORT_DIRECTORY[key]
                    matches.append((m.start(), info))
            matches.sort(key=lambda x: x[0])
            unique_airports = []
            seen_iata = set()
            for _, info in matches:
                if info[0] not in seen_iata:
                    seen_iata.add(info[0])
                    unique_airports.append(info)
            if len(unique_airports) >= 2:
                orig_from_query = unique_airports[0]
                dest_from_query = unique_airports[1]

    orig_resolved = (resolve_airport(origin) if origin else None) or orig_from_query
    dest_resolved = (resolve_airport(destination) if destination else None) or dest_from_query

    # Inherit from channel's last route on follow-up prompts
    if not (orig_resolved and dest_resolved):
        if not extract_flight_numbers(q_text):
            last_route = get_channel_last_route()
            if last_route:
                orig_resolved = orig_resolved or last_route[0]
                dest_resolved = dest_resolved or last_route[1]

    route_changed = bool(
        prev_state
        and prev_state.origin
        and prev_state.destination
        and orig_resolved
        and dest_resolved
        and (orig_resolved[0] != prev_state.origin[0] or dest_resolved[0] != prev_state.destination[0])
    )

    # Parse dates, month windows, and stay duration ('for 3 weeks', 'for 10 days', 'for a month')
    is_explicit_one_way = bool(
        re.search(r"\b(?:one[\s\-]?way)\b", q_low)
        or (trip_type_arg and "one" in trip_type_arg.lower())
    )
    is_explicit_round_trip = bool(
        re.search(r"\b(?:round[\s\-]?trip|return\s+trip|with\s+return|returning|back\s+in)\b", q_low)
        or (trip_type_arg and "round" in trip_type_arg.lower())
    )

    query_explicit_dates = _extract_all_dates_from_text(q_text, now) if q_text else []
    query_natural_date = parse_natural_date(q_text, now) if q_text else None
    query_has_explicit_day = bool(query_explicit_dates or query_natural_date)
    query_has_month_name = bool(q_text and re.search(rf"\b({month_pattern})\b", q_low))
    is_window_followup = bool(
        re.search(
            r"\b(?:those\s+months?|that\s+month|those\s+dates?|that\s+window|during\s+then|same\s+months?|same\s+dates?)\b",
            q_low,
        )
    )
    has_flex_phrase = bool(
        re.search(
            r"\b(?:best\s+days?|cheapest\s+days?|best\s+dates?|cheapest\s+dates?|best\s+time|cheapest\s+time|"
            r"best\s+flights?|best\s+flight\s+options?|best\s+options?|best\s+price|when\s+to\s+fly|when\s+should\s+i\s+fly|"
            r"when\s+is\s+the\s+(?:best|cheapest|shortest|fastest)|whens\s+best|"
            r"when\s+are\s+(?:the\s+)?(?:direct|nonstop|cheapest|best|shortest|fastest)|which\s+days)\b",
            q_low,
        )
    )

    dep_date: Optional[str] = None
    ret_date: Optional[str] = None
    dep_month_window: Optional[Tuple[str, str, str]] = None
    ret_month_window: Optional[Tuple[str, str, str]] = None

    # 1. Explicit day-of-month dates in query or tool args (when not a month-only query or constraint-only follow-up)
    is_constraint_only_followup = bool(
        prev_state
        and not route_changed
        and q_text
        and not query_has_explicit_day
        and not query_has_month_name
    )

    # Extract optional trip duration in days ("for 3 weeks", "for 10 days", "for a month")
    trip_days: Optional[int] = None
    m_stay = re.search(r"\bfor\s+(\d+)\s*(day|days|week|weeks|month|months)\b", q_low)
    if m_stay:
        n_val = int(m_stay.group(1))
        unit_s = m_stay.group(2)
        trip_days = n_val * 30 if "month" in unit_s else (n_val * 7 if "week" in unit_s else n_val)
    elif re.search(r"\bfor\s+a\s+week\b", q_low):
        trip_days = 7
    elif re.search(r"\bfor\s+a\s+month\b", q_low):
        trip_days = 30
    elif is_constraint_only_followup and prev_state:
        trip_days = prev_state.trip_days

    if query_has_explicit_day:
        if len(query_explicit_dates) >= 2:
            dep_date = query_explicit_dates[0]
            if not is_explicit_one_way and query_explicit_dates[1] != dep_date:
                ret_date = query_explicit_dates[1]
        elif len(query_explicit_dates) == 1:
            is_ret_date_update = bool(
                prev_state
                and not route_changed
                and prev_state.dep_date
                and re.search(r"\b(?:return(?:ing)?|back|inbound)\b", q_low)
                and not re.search(r"\b(?:leav(?:e|ing)|depart(?:ing)?|outbound)\b", q_low)
            )
            if is_ret_date_update and not is_explicit_one_way:
                dep_date = prev_state.dep_date
                dep_month_window = prev_state.dep_month_window
                ret_date = query_explicit_dates[0]
            else:
                dep_date = query_explicit_dates[0]
        else:
            dep_date = query_natural_date

        if return_date and not ret_date and not is_explicit_one_way:
            ret_date = parse_natural_date(return_date, now)
    elif not is_constraint_only_followup and not query_has_month_name and (date or return_date):
        # Tool args supplied without month names in q_text (e.g. direct API call or date="2026-11-15")
        m_ym_date = re.match(r"^\s*(20\d{2})-(\d{2})\s*$", date or "")
        if m_ym_date:
            yr, mo = int(m_ym_date.group(1)), int(m_ym_date.group(2))
            if 1 <= mo <= 12:
                dep_month_window = _month_window_for(yr, mo, now)
                dep_date = _representative_tuesday(yr, mo, now)
        elif date:
            date_arg_dates = _extract_all_dates_from_text(date, now)
            if len(date_arg_dates) >= 2 and not is_explicit_one_way:
                dep_date = date_arg_dates[0]
                ret_date = date_arg_dates[1]
            elif len(date_arg_dates) == 1:
                dep_date = date_arg_dates[0]
            else:
                dep_date = parse_natural_date(date, now)
        if return_date and not ret_date and not is_explicit_one_way:
            ret_date = parse_natural_date(return_date, now)

    # 2. Month-level or Multi-Month Extraction when no specific day number was in q_text
    if not query_has_explicit_day and query_has_month_name:
        has_next_year = bool(re.search(r"\bnext\s+year\b", q_low))
        # Authoritative year comes from q_text when q_text is present so LLM-hallucinated tool-arg years never override "in december"
        year_source = q_text if q_text else f"{date or ''} {return_date or ''}"
        explicit_years = [int(y) for y in re.findall(r"\b(20\d{2})\b", year_source)]

        raw_month_matches = list(re.finditer(rf"\b({month_pattern})\.?(?:\s+(20\d{{2}}))?\b", q_text, re.IGNORECASE))
        unique_months: list[Tuple[int, Optional[int]]] = []
        for m in raw_month_matches:
            mo_num = _MONTH_MAP[m.group(1).lower()]
            yr_num = int(m.group(2)) if m.group(2) else None
            if not unique_months or unique_months[-1][0] != mo_num:
                unique_months.append((mo_num, yr_num))

        if len(unique_months) >= 2:
            mo1, yr1_opt = unique_months[0]
            mo2, yr2_opt = unique_months[1]
            if yr1_opt:
                yr1 = yr1_opt
            elif yr2_opt:
                yr1 = yr2_opt - 1 if mo1 > mo2 else yr2_opt
            elif explicit_years:
                yr1 = explicit_years[0]
            elif has_next_year:
                yr1 = now.year + 1
            else:
                yr1 = now.year + 1 if (now.year, mo1) < (now.year, now.month) else now.year

            if yr2_opt:
                yr2 = yr2_opt
            else:
                yr2 = yr1 + 1 if mo2 < mo1 else yr1

            dep_month_window = _month_window_for(yr1, mo1, now)
            dep_date = _representative_tuesday(yr1, mo1, now)
            if not is_explicit_one_way:
                ret_month_window = _month_window_for(yr2, mo2, now)
                ret_date = _representative_tuesday(yr2, mo2, now)

        elif len(unique_months) == 1:
            mo1, yr1_opt = unique_months[0]
            # Check if this single month is a return-month follow-up (e.g. "i need round trip back in August")
            is_return_month_followup = bool(
                prev_state
                and not route_changed
                and (prev_state.dep_month_window or prev_state.dep_date)
                and not is_explicit_one_way
                and (
                    re.search(rf"\b(?:return(?:ing)?|back|inbound|coming\s+back|fly(?:ing)?\s+back)\b.*\b({month_pattern})\b", q_low)
                    or (
                        is_explicit_round_trip
                        and not re.search(rf"\b(?:leav(?:e|ing)|depart(?:ing)?|outbound|from)\s+({month_pattern})\b", q_low)
                    )
                )
            )
            if is_return_month_followup and prev_state:
                dep_month_window = prev_state.dep_month_window
                dep_date = prev_state.dep_date
                base_dep_yr = int(dep_date[:4]) if dep_date else (now.year + 1)
                base_dep_mo = int(dep_date[5:7]) if dep_date else 1
                ret_yr = yr1_opt or (explicit_years[0] if explicit_years else (base_dep_yr + 1 if mo1 < base_dep_mo else base_dep_yr))
                ret_month_window = _month_window_for(ret_yr, mo1, now)
                ret_date = _representative_tuesday(ret_yr, mo1, now)
            else:
                if yr1_opt:
                    yr1 = yr1_opt
                elif explicit_years:
                    yr1 = explicit_years[0]
                elif has_next_year:
                    yr1 = now.year + 1
                else:
                    yr1 = now.year + 1 if (now.year, mo1) < (now.year, now.month) else now.year
                dep_month_window = _month_window_for(yr1, mo1, now)
                dep_date = _representative_tuesday(yr1, mo1, now)

    # 3. Inherit dates/windows from channel's FlightSearchState on constraint/sort follow-ups ("1 stop only", "during those months")
    if not dep_date and not dep_month_window and prev_state and not route_changed:
        if is_constraint_only_followup or is_window_followup:
            dep_month_window = prev_state.dep_month_window
            dep_date = prev_state.dep_date
            if not is_explicit_one_way:
                ret_month_window = prev_state.ret_month_window
                ret_date = prev_state.ret_date

    wants_latest_horizon = bool(
        re.search(r"\b(?:latest|farthest|furthest|how\s+far\s+out|last\s+available|latest\s+return|latest\s+flight)\b", q_low)
    )

    # Detect sort preference ('shortest', 'cheapest', or 'best')
    if sort_by_arg and sort_by_arg.lower() in (SortPreference.SHORTEST, SortPreference.CHEAPEST, SortPreference.BEST):
        sort_preference = sort_by_arg.lower()
    elif re.search(r"\b(?:shortest|fastest|quickest|least\s+travel\s+time|minimum\s+layover)\b", q_low):
        sort_preference = SortPreference.SHORTEST
    elif re.search(r"\b(?:cheapest|lowest\s+price|lowest\s+fare|budget)\b", q_low) and not re.search(r"\bbest\b", q_low):
        sort_preference = SortPreference.CHEAPEST
    elif is_constraint_only_followup and prev_state:
        sort_preference = prev_state.sort_preference
    else:
        sort_preference = SortPreference.BEST

    # Detect direct/nonstop schedule discovery intent
    wants_direct_schedule = bool(
        re.search(
            r"\b(?:when\s+are\s+(?:the\s+)?(?:direct|nonstop|non-stop)|when\s+does\b.*\b(?:direct|nonstop)|"
            r"which\s+days\b.*\b(?:direct|nonstop)|direct\s+flights?|nonstop\s+flights?|non-stop\s+flights?|"
            r"fly\s+direct|direct\s+schedule|has\s+direct\s+flights?)\b",
            q_low,
        )
    )

    # Stop filter detection (0 = nonstop, 1 = max 1 stop, 2 = max 2 stops)
    explicit_relax_stops = bool(re.search(r"\b(?:any\s+stops?|all\s+flights?)\b", q_low))
    if max_stops_arg is not None and int(max_stops_arg) in (0, 1, 2):
        max_stops: Optional[int] = int(max_stops_arg)
    elif re.search(r"\b(?:nonstop|non-stop|direct|0[\s\-]?stops?|zero[\s\-]?stops?|no[\s\-]?stops?)\b", q_low):
        max_stops = 0
    elif re.search(r"\b(?:1[\s\-]?stops?|one[\s\-]?stops?|single[\s\-]?stops?|1[\s\-]?layover|single[\s\-]?layover)\b", q_low):
        max_stops = 1
    elif re.search(r"\b(?:2[\s\-]?stops?|two[\s\-]?stops?)\b", q_low):
        max_stops = 2
    elif prev_state and not route_changed and not explicit_relax_stops:
        max_stops = prev_state.max_stops
    else:
        max_stops = None

    # Default departure date to 14 days out if not specified so single-date price searches always succeed
    defaulted_date = bool(not query_has_explicit_day and (dep_month_window is not None or not dep_date))
    if not dep_date:
        if dep_month_window:
            dep_date = dep_month_window[0]
        else:
            dep_date = (now + timedelta(days=14)).strftime("%Y-%m-%d")
        defaulted_date = True

    # If stay duration ('for 3 weeks', 'for 10 days') was given, automatically compute return date & set round-trip
    if trip_days and not is_explicit_one_way and not ret_date and dep_date:
        try:
            ret_date = (datetime.strptime(dep_date, "%Y-%m-%d").date() + timedelta(days=trip_days)).strftime("%Y-%m-%d")
        except ValueError:
            pass

    trip_type = TripType.ROUND_TRIP if (ret_date or ret_month_window or (trip_days and not is_explicit_one_way)) else TripType.ONE_WAY

    wants_flexible_scan = bool(
        not wants_latest_horizon
        and (
            dep_month_window is not None
            or ret_month_window is not None
            or (has_flex_phrase and defaulted_date)
            or (wants_direct_schedule and defaulted_date)
            or (sort_preference == SortPreference.SHORTEST and defaulted_date)
            or (max_stops is not None and is_window_followup)
            or (trip_days is not None and defaulted_date)
        )
    )

    if dep_month_window:
        flex_start_date, flex_end_date, dep_label = dep_month_window
        if ret_month_window:
            flex_label = f"{dep_label} – {ret_month_window[2]}"
        elif trip_days:
            flex_label = f"{dep_label} (~{trip_days}-Day Round-Trip)"
        else:
            flex_label = dep_label
    elif not defaulted_date and dep_date:
        base_d = datetime.strptime(dep_date, "%Y-%m-%d").date()
        start_d = max((now + timedelta(days=1)).date(), base_d - timedelta(days=2))
        end_d = start_d + timedelta(days=13)
        flex_start_date = start_d.strftime("%Y-%m-%d")
        flex_end_date = end_d.strftime("%Y-%m-%d")
        flex_label = f"2-Week Window around {dep_date} ({flex_start_date} to {flex_end_date})"
    else:
        start_d = (now + timedelta(days=7)).date()
        end_d = (now + timedelta(days=20)).date()
        flex_start_date = start_d.strftime("%Y-%m-%d")
        flex_end_date = end_d.strftime("%Y-%m-%d")
        flex_label = f"Next 2-Week Window ({flex_start_date} to {flex_end_date})"

    # Cabin class detection
    if "first class" in q_low:
        cabin = CabinClass.FIRST
    elif "business" in q_low:
        cabin = CabinClass.BUSINESS
    elif "premium economy" in q_low or "premium-economy" in q_low:
        cabin = CabinClass.PREMIUM_ECONOMY
    elif re.search(r"\beconomy\b", q_low):
        cabin = CabinClass.ECONOMY
    elif prev_state and not route_changed:
        cabin = prev_state.cabin
    else:
        cabin = CabinClass.ECONOMY

    # Currency detection (default CAD for Canadian airports, else USD unless specified)
    if re.search(r"\b(?:usd|us\s*dollars?)\b", q_low):
        currency = "USD"
    elif re.search(r"\b(?:eur|euros?)\b", q_low):
        currency = "EUR"
    elif re.search(r"\b(?:gbp|pounds?)\b", q_low):
        currency = "GBP"
    elif re.search(r"\b(?:cad|canadian)\b", q_low):
        currency = "CAD"
    elif prev_state and not route_changed and prev_state.currency:
        currency = prev_state.currency
    elif (orig_resolved and orig_resolved[3] == "Canada") or (dest_resolved and dest_resolved[3] == "Canada"):
        currency = "CAD"
    else:
        currency = "USD"

    # Persist updated conversational FlightSearchState for this channel
    if orig_resolved and dest_resolved and not extract_flight_numbers(q_text):
        new_state = FlightSearchState(
            origin=orig_resolved,
            destination=dest_resolved,
            dep_date=dep_date,
            ret_date=ret_date,
            dep_month_window=dep_month_window,
            ret_month_window=ret_month_window,
            trip_type=trip_type,
            max_stops=max_stops,
            sort_preference=sort_preference,
            trip_days=trip_days,
            cabin=cabin,
            currency=currency,
        )
        update_channel_flight_state(new_state)
        record_channel_last_window(
            {
                "month_window": dep_month_window,
                "ret_month_window": ret_month_window,
                "dep_date": dep_date,
                "ret_date": ret_date,
            }
        )

    return_flex_label = (
        ret_month_window[2]
        if ret_month_window
        else (f"~{trip_days}-Day Stay" if (trip_days and ret_date) else None)
    )

    return {
        "origin": orig_resolved,
        "destination": dest_resolved,
        "date": dep_date,
        "return_date": ret_date,
        "trip_type": trip_type,
        "defaulted_date": defaulted_date,
        "wants_latest_horizon": wants_latest_horizon,
        "wants_flexible_scan": wants_flexible_scan,
        "wants_direct_schedule": wants_direct_schedule,
        "sort_preference": sort_preference,
        "flex_start_date": flex_start_date,
        "flex_end_date": flex_end_date,
        "flex_label": flex_label,
        "dep_month_window": dep_month_window,
        "ret_month_window": ret_month_window,
        "return_flex_start_date": ret_month_window[0] if ret_month_window else None,
        "return_flex_end_date": ret_month_window[1] if ret_month_window else None,
        "return_flex_label": return_flex_label,
        "trip_days": trip_days,
        "cabin": cabin,
        "currency": currency,
        "max_stops": max_stops,
    }

