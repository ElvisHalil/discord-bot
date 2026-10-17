from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from src.skills.flights.aggregators import GoogleFlightsPriceAggregator, LiveFlightStatusAggregator
from src.skills.flights.airports import extract_flight_numbers, parse_route_from_query
from src.utils.timezones import get_local_now

_price_aggregator = GoogleFlightsPriceAggregator()
_status_aggregator = LiveFlightStatusAggregator()
_FLIGHT_EXECUTOR = ThreadPoolExecutor(max_workers=6, thread_name_prefix="flights_worker")


def skill_flight_info(
    query: Optional[str] = None,
    origin: Optional[str] = None,
    destination: Optional[str] = None,
    date: Optional[str] = None,
    return_date: Optional[str] = None,
    flight_number: Optional[str] = None,
    cabin: Optional[str] = None,
    currency: Optional[str] = None,
    max_stops: Optional[int] = None,
    sort_by: Optional[str] = None,
    trip_type: Optional[str] = None,
) -> str:
    """
    Use this skill to:
      1. Search live flight ticket prices, best/shortest/cheapest itineraries, flexible-date ('best day to fly')
         calendars, round-trip month windows, and direct/1-stop flight operating schedules (via Google Flights).
      2. Check real-time commercial flight status, gate/terminal info, delays, and live airborne radar telemetry
         for any flight number (via Flightradar24 + ADSB.lol).

    Args:
        query: The user's exact flight question or follow-up (e.g. 'planning trip from Edmonton to Zagreb from May to August next year',
               '1 stop only', 'i need round trip back in August', 'status of AC850'). Always pass the user's message here.
        origin: Departure city or 3-letter IATA airport code (e.g. 'Calgary' or 'YYC', 'Edmonton' or 'YEG').
        destination: Arrival city or 3-letter IATA airport code (e.g. 'Amsterdam' or 'AMS', 'Zagreb' or 'ZAG').
        date: Departure date ('YYYY-MM-DD' or natural language like 'tomorrow', 'Nov 21, 2026'). Omit if user only named a month.
        return_date: Optional return date ('YYYY-MM-DD' or natural language) for round-trip searches. Omit if user only named a month.
        flight_number: Specific commercial flight number or ICAO callsign to check live status (e.g. 'AC850', 'WS102', 'UA123').
        cabin: Cabin class ('economy', 'premium-economy', 'business', 'first').
        currency: 3-letter currency code (e.g. 'CAD', 'USD', 'EUR', 'GBP').
        max_stops: Optional stop filter (0 for nonstop/direct only, 1 for max 1 stop, 2 for max 2 stops).
        sort_by: Optional ranking preference ('best', 'shortest', or 'cheapest').
        trip_type: Optional trip type ('round-trip' or 'one-way').
    """
    q_raw = (query or "").strip()
    f_num_raw = (flight_number or "").strip()
    print(
        f"✈️ [Executing Flight Info Skill] Query: '{q_raw}', Origin: {origin}, "
        f"Dest: {destination}, Date: {date}, Return: {return_date}, Stops: {max_stops}, Flight#: {f_num_raw}"
    )

    flight_codes = []
    if f_num_raw:
        extracted = extract_flight_numbers(f_num_raw)
        flight_codes.extend(extracted if extracted else [f_num_raw])
    if q_raw:
        for code in extract_flight_numbers(q_raw):
            if code not in flight_codes:
                flight_codes.append(code)

    tasks = []
    for fc in flight_codes[:4]:
        tasks.append(_FLIGHT_EXECUTOR.submit(_status_aggregator.get_flight_status, fc))

    if not flight_codes or origin or destination:
        tasks.append(
            _FLIGHT_EXECUTOR.submit(
                _price_aggregator.search_prices,
                query=q_raw,
                origin=origin,
                destination=destination,
                date=date,
                return_date=return_date,
                cabin=cabin,
                currency=currency,
                max_stops=max_stops,
                sort_by=sort_by,
                trip_type=trip_type,
            )
        )

    sections = [fut.result() for fut in tasks]
    header = f"📅 CURRENT REFERENCE DATE: {get_local_now().strftime('%A, %B %d, %Y')}"
    output = f"{header}\n\n" + "\n=============================================\n\n".join(sections)

    output += """
=== CRITICAL INSTRUCTIONS FOR YOU (THE LLM) ===
1. Present the exact flight prices, airlines, departure/arrival times, durations, stops, layovers, and aircraft models from the data above.
2. When reporting Google Flights ticket prices or Flexible-Date / Direct / 1-Stop Schedule Scans:
   - Strictly respect the user's **Trip Type** (Round-Trip vs. One-Way) and **Stop Filter** (e.g. `Max 1 Stop(s)`). When the user asks for `1 stop only`, NEVER recommend 2-stop flights.
   - If the report includes a **CONFIRMED 1-STOP GATEWAY SCHEDULE** (e.g. `YEG -> AMS -> ZAG` for dates >6 months out):
     * Present the **🛫 Outbound 1-Stop Schedule** (`Leg 1: Origin -> Hub` + `Leg 2: Hub -> Destination`) AND, for Round-Trips, the **🛬 Return 1-Stop Schedule** (`Return Segment 1: Destination -> Hub` + `Return Segment 2: Hub -> Origin`).
     * NEVER confuse `Outbound Leg 2` (`Hub -> Destination`, e.g. `AMS -> ZAG`) with the return trip back home (`ZAG -> AMS -> YEG`)!
     * Quote the **🔄 Published 1-Stop Round-Trip Benchmark** (or 6-month horizon fare) as the expected single-ticket price once the 6-month combined ticketing window opens, and **NEVER tell the user to fly on the 6-month benchmark date (e.g. April) when they asked for May–August**!
   - Always include the clickable **[Google Flights](<https://www.google.com/travel/flights/search?tfs=...>)` booking link** for the user's requested dates.
3. When reporting Live Flight Status:
   - Clearly state whether the flight is **Scheduled**, **En Route / Airborne**, **Landed**, **Delayed**, or **Cancelled**, along with Terminal/Gate info and local departure/arrival times.
   - For airborne flights, include live radar telemetry (Altitude in ft, Ground Speed in kts/mph/kmh, route completion %, remaining distance, and the clickable Flightradar24 / ADSB.lol live radar map links).
4. Mention that the user can track any live flight or set a quality-filtered (or direct-only) ticket price-drop alert anytime by asking (e.g. "track flight AC850" or "track price from Edmonton to Amsterdam under $900").
"""
    return output

