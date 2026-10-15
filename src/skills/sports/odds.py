from typing import Optional
from src.skills.sports.aggregators.odds import OddsAggregator

_odds_aggregator = OddsAggregator()


def skill_betting_odds(query: str, sport: Optional[str] = "sports") -> str:
    """
    Fetch current match betting odds, moneyline lines, spreads, totals, and implied win probabilities for sports and esports.

    Args:
        query: Matchup, team, event name, or 'tracked games' (e.g. 'Washington Capitals vs Montreal Canadiens', 'Colorado Avalanche vs Dallas Stars', 'Real Madrid', 'Falcons vs Ninjas in Pyjamas').
        sport: Sport category, default 'sports' (e.g. 'nhl', 'nba', 'nfl', 'soccer', 'ucl', 'mlb', 'f1', 'cs2', 'esports').
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
