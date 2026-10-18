"""
Centralized, concise LLM instruction builders for the sports skill package.
"""

from src.skills.sports.models import MatchSnapshot, MatchState


def build_live_tracker_instructions(snap: MatchSnapshot) -> str:
    """Build concise LLM instructions for live sports match tracking."""
    if snap.state == MatchState.IN:
        return (
            f"\n\n=== CRITICAL INSTRUCTION FOR YOU (THE LLM) ===\n"
            f"1. Live tracking is NOW ACTIVE in the background for {snap.home_team} vs {snap.away_team}.\n"
            f"2. Match is CURRENTLY LIVE at {snap.status_detail} ({snap.home_team} {snap.home_score} - {snap.away_score} {snap.away_team}) — NOT finished.\n"
            f"3. Do NOT say the game ended at {snap.status_detail}; ignore older scores or minutes from earlier turns."
        )
    return (
        f"\n\n=== CRITICAL INSTRUCTION FOR YOU (THE LLM) ===\n"
        f"1. Live tracking is NOW ACTIVE in the background for {snap.home_team} vs {snap.away_team}."
    )


def build_sports_llm_instructions(today_str: str, guidance: str) -> str:
    """Build concise LLM instructions for general sports info responses."""
    return (
        "\n=== CRITICAL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
        f"1. TODAY'S DATE: {today_str}. Anchor all relative dates ('today', 'tomorrow', 'yesterday', 'this week') on this date.\n"
        "2. Use exact local dates/times shown in the data; never alter days of the week.\n"
        "3. Report any 🔴 LIVE NOW match first with current score, clock, scorers, and stats; a live minute like (55') is ongoing, NOT final.\n"
        f"4. {guidance} Format cleanly with bold headers and bullet points.\n"
    )


def build_cs_llm_instructions() -> str:
    """Build concise LLM instructions for Counter-Strike esports responses."""
    return (
        "=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
        "1. Synthesize a cohesive overview highlighting the next S-Tier tournament (`[Name](<https://liquipedia.net/...>)`), exact match times from `MATCH SCHEDULE & RESULTS (Exact Times)`, recent champions, and top HLTV rankings; DO NOT create a prominent empty section if no matches are live.\n"
        "2. NEVER output pseudo-links (`<event_page:...>`) or raw Unix epoch tags (`<t:...:t>`).\n"
        "3. Only list active teams from `PARTICIPATING TEAMS`; list player rosters only when requested.\n"
        "4. For player stats, report K/D, ADR, KPR/DPR, HS%, Opening Kills/Rd, and HLTV Rating 3.0 (`0-100` role scores are percentiles).\n"
        "5. For live YouTube streams, place the raw YouTube URL on its own blank line.\n"
    )


def build_odds_llm_instructions() -> str:
    """Build concise LLM instructions for sports and esports betting odds responses."""
    return (
        "=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
        "1. Always present **Decimal Odds** (e.g. `2.50`) alongside implied win probabilities (`~40.0%`), identifying the favorite.\n"
        "2. Do NOT convert to American moneyline odds (`+150`/`-200`) unless explicitly requested.\n"
        "3. Never invent URLs; if lines haven't opened yet, state so plainly.\n"
    )
