from typing import Optional
from src.skills.sports.aggregators.odds import OddsAggregator

_odds_aggregator = OddsAggregator()


def skill_betting_odds(query: str, sport: Optional[str] = "esports") -> str:
    """
    Fetch current match betting odds, moneyline lines, and implied win probabilities for sports and esports.

    Args:
        query: Matchup or event name (e.g. 'Falcons vs Ninjas in Pyjamas', '9z vs Aurora', 'Verstappen US GP').
        sport: Sport category, default 'esports' (e.g. 'cs2', 'esports', 'f1', 'nfl', 'nba', 'soccer').
    """
    print(f"🎲 [Executing Betting Odds Skill] Matchup: '{query}' (Sport: '{sport}')")
    odds_data = _odds_aggregator.get_odds(query, sport=sport)

    output = odds_data
    output += "=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
    output += "1. Present the odds, moneyline lines, and win probabilities clearly to the user.\n"
    output += "2. Clearly identify the favorite and underdog with their respective prices.\n"
    output += "3. NEVER invent fake URLs or hallucinate betting links. Only link sources provided in the data above.\n"
    output += "4. If no odds are posted yet, explain that lines haven't opened without making excuses or mentioning tools.\n"
    return output
