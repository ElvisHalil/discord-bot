from typing import Optional

from src.skills.sports.aggregators.odds import OddsAggregator
from src.skills.sports.enums import SportId
from src.skills.sports.templates import build_odds_llm_instructions

_odds_aggregator = OddsAggregator()


def skill_betting_odds(query: str, sport: Optional[str] = SportId.SPORTS) -> str:
    """
    Fetch current match betting odds, decimal payout multipliers, and implied win probabilities for sports, F1, and esports.

    Args:
        query: Matchup, team, league, event name, or 'tracked games' (e.g. 'Washington Capitals vs Montreal Canadiens', 'soccer odds', 'nhl odds', 'f1 odds', 'Real Madrid', 'Aurora vs 9z').
        sport: Sport category, default 'sports' (e.g. 'nhl', 'nba', 'nfl', 'soccer', 'ucl', 'epl', 'mlb', 'f1', 'cs2', 'esports').
    """
    print(f"🎲 [Executing Betting Odds Skill] Matchup: '{query}' (Sport: '{sport}')")
    odds_data = _odds_aggregator.get_odds(query, sport=sport)
    return odds_data + build_odds_llm_instructions()
