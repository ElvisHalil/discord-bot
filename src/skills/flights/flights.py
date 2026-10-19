from typing import Optional

from src.skills.flights.aggregators import GoogleFlightsPriceAggregator, LiveFlightStatusAggregator
from src.skills.flights.engine.resolver import extract_flight_numbers, parse_route_from_query
from src.skills.flights.templates import build_flight_search_instructions
from src.utils.timezones import get_local_now
from src.utils.workers import submit_to_worker

_price_aggregator = GoogleFlightsPriceAggregator()
_status_aggregator = LiveFlightStatusAggregator()


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
    Search live flight ticket prices, best/shortest/cheapest itineraries, flexible-date calendars, and direct flight schedules (Google Flights), OR check real-time flight status, gates, delays, and airborne radar telemetry (Flightradar24 + ADSB.lol).

    Args:
        query: Full flight question or follow-up (e.g. 'flights from Calgary to Tokyo in Nov', '1 stop only', 'status of AC850'). Always pass the user's message here.
        origin: Departure city or 3-letter IATA airport code (e.g. 'Calgary', 'YYC', 'JFK').
        destination: Arrival city or 3-letter IATA airport code (e.g. 'Tokyo', 'HND', 'LHR').
        date: Departure date ('YYYY-MM-DD' or natural language). Omit if user only named a month.
        return_date: Optional return date ('YYYY-MM-DD' or natural language) for round-trip searches.
        flight_number: Commercial flight number or ICAO callsign for live status (e.g. 'AC850', 'BA117').
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
        tasks.append(submit_to_worker(_status_aggregator.get_flight_status, fc))

    if not flight_codes or origin or destination:
        tasks.append(
            submit_to_worker(
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
    output += build_flight_search_instructions()
    return output

