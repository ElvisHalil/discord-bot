"""
Centralized, concise LLM instruction builders for the flights skill package.
"""


def build_flight_search_instructions() -> str:
    """Build concise LLM instructions for flight search and live status tool outputs."""
    return (
        "\n=== CRITICAL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
        "1. Present exact flight prices, travel dates, airlines, departure/arrival times, durations, stops, layovers, and aircraft models from the data above.\n"
        "2. ALWAYS include concrete travel date(s) (e.g. `Tue, Dec 01, 2026 ↔ Tue, Dec 22, 2026` or `2026-12-01`) on EVERY recommended option.\n"
        "3. NEVER use LaTeX or math mode (`$...$`, `\\rightarrow`, `\\leftrightarrow`) — use plain text with Unicode arrows (`→`, `↔`, `~$1,671 CAD`).\n"
        "4. Respect the user's Trip Type (Round-Trip vs. One-Way) and Stop Filter (`Max 1 Stop(s)`). For 1-Stop Gateway Schedules >6 months out, present Outbound Leg 1 + Leg 2, Return Segment 1 + Segment 2, and the 6-month Round-Trip Benchmark without telling the user to fly on the benchmark date.\n"
        "5. Always attach the exact clickable `[Google Flights](<https://www.google.com/travel/flights/search?tfs=...>)` booking link matching that date pair.\n"
        "6. For Live Flight Status, report status, terminal/gate, local times, and airborne telemetry (altitude, ground speed, completion %, Flightradar24/ADSB.lol links).\n"
    )

