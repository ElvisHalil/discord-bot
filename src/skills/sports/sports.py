from typing import Optional
from src.skills.sports.aggregators import F1Aggregator, OddsAggregator

_f1_aggregator = F1Aggregator()
_odds_aggregator = OddsAggregator()

def skill_sports_info(sport: str, query: Optional[str] = "schedule") -> str:
    """
    Fetch current sports information (schedule, upcoming matches/races, or betting odds).
    Currently supported sports: 'f1', 'formula 1', or odds across sports.

    Args:
        sport: The sport to look up (e.g. 'f1', 'cs2', 'nfl', 'nba', 'soccer').
        query: What to fetch ('schedule' or 'odds' / matchup name).
    """
    print(f"🏎️ [Executing Sports Info Skill] Sport: {sport}, Query: {query}")
    sport_lower = sport.lower().strip()
    query_lower = (query or "schedule").lower().strip()

    if "odd" in query_lower or "bet" in query_lower:
        return _odds_aggregator.get_odds(query, sport=sport_lower)

    if sport_lower not in ["f1", "formula 1", "formula1"]:
        return f"Currently, F1 schedule and multi-sport betting odds are supported for sports_info. For other details on {sport}, please use search_web."

    schedule_data = _f1_aggregator.get_schedule()

    output = schedule_data
    output += "\n=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
    output += "Review the F1 schedule and inform the user. When mentioning times, remind them they are in UTC unless you convert them locally.\n"
    return output
