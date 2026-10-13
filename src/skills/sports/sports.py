from typing import Optional
from src.skills.sports.aggregators import F1Aggregator

_f1_aggregator = F1Aggregator()

def skill_sports_info(sport: str, query: Optional[str] = "schedule") -> str:
    """
    Fetch current sports information (schedule, upcoming matches/races). 
    Currently supported sports: 'f1'

    Args:
        sport: The sport to look up. Currently only 'f1' is supported.
        query: What to fetch (default 'schedule').
    """
    print(f"🏎️ [Executing Sports Info Skill] Sport: {sport}, Query: {query}")
    sport = sport.lower()
    if sport not in ["f1", "formula 1", "formula1"]:
        return f"Currently, only F1 is supported for the sports_info skill. I don't have direct data for {sport} yet. Please use the web search tool instead."
        
    schedule_data = _f1_aggregator.get_schedule()
    
    output = schedule_data
    output += "\n=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
    output += "Review the F1 schedule and inform the user. When mentioning times, remind them they are in UTC unless you convert them locally.\n"
    return output
