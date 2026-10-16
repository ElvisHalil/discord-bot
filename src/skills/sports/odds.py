from typing import Optional
from src.skills.sports.aggregators.odds import OddsAggregator

_odds_aggregator = OddsAggregator()


def skill_betting_odds(query: str, sport: Optional[str] = "sports") -> str:
    """
    Fetch current match betting odds, decimal payout multipliers, and implied win probabilities for sports, F1, and esports.

    Args:
        query: Matchup, team, league, event name, or 'tracked games' (e.g. 'Washington Capitals vs Montreal Canadiens', 'soccer odds', 'nhl odds', 'f1 odds', 'Real Madrid', 'Aurora vs 9z').
        sport: Sport category, default 'sports' (e.g. 'nhl', 'nba', 'nfl', 'soccer', 'ucl', 'epl', 'mlb', 'f1', 'cs2', 'esports').
    """
    print(f"🎲 [Executing Betting Odds Skill] Matchup: '{query}' (Sport: '{sport}')")
    odds_data = _odds_aggregator.get_odds(query, sport=sport)

    output = odds_data
    output += "=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
    output += "1. Always present odds in clean **Decimal Odds** format (e.g. `2.50`, meaning a 2.5x total return on your wager) alongside their implied win probabilities (`~40.0%`).\n"
    output += "2. Do NOT convert to or output American moneyline odds (`+150` / `-200`) unless the user explicitly asked for American odds.\n"
    output += "3. Clearly identify the favorite and each contender's best available decimal odds using the clean bullet layout above.\n"
    output += "4. NEVER invent fake URLs or hallucinate betting links. Only link sources provided in the data above.\n"
    output += "5. If no odds are posted yet, explain that lines haven't opened without making excuses or mentioning tools.\n"
    return output
