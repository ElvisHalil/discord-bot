from typing import Optional
from src.skills.sports.aggregators import (
    F1Aggregator,
    SoccerAggregator,
    NbaAggregator,
    HockeyAggregator,
    OddsAggregator,
)

_f1_aggregator = F1Aggregator()
_soccer_aggregator = SoccerAggregator()
_nba_aggregator = NbaAggregator()
_hockey_aggregator = HockeyAggregator()
_odds_aggregator = OddsAggregator()

def skill_sports_info(sport: str, query: Optional[str] = "schedule") -> str:
    """
    Fetch live sports information including match scores, goal scorers, quarter/period linescores,
    upcoming schedules, and championship standings.

    Supported sports:
    - 'f1' / 'formula 1': Grand Prix schedule, session times (FP1-3, Qual, Race), results, driver & constructor standings.
    - 'soccer' / 'football' / 'epl' / 'ucl' / 'laliga' / 'seriea' / 'bundesliga' / 'mls': Live matches, scores, goal scorers, fixtures, and standings.
    - 'nba' / 'basketball': Live scores, quarter linescores, today's schedule, and Eastern/Western conference standings.
    - 'nhl' / 'hockey': Live scores, period linescores, today's matchups, and conference standings.
    - 'odds' / 'betting': Live betting odds, moneylines, and match favorites.

    Args:
        sport: The sport to look up (e.g. 'f1', 'soccer', 'nba', 'nhl', 'epl', 'ucl').
        query: What to fetch ('schedule', 'scores', 'standings', 'results', team name, or date like 'yesterday'/'tomorrow').
    """
    sport_raw = (sport or "").strip()
    query_raw = (query or "schedule").strip()
    print(f"🏟️ [Executing Sports Info Skill] Sport: {sport_raw}, Query: {query_raw}")

    sport_lower = sport_raw.lower()
    query_lower = query_raw.lower()
    combined = f"{sport_lower} {query_lower}"

    # 1. Betting odds check
    if "odd" in query_lower or "bet" in query_lower or sport_lower in ("odds", "betting"):
        return _odds_aggregator.get_odds(query_raw, sport=sport_lower)

    # 2. Formula 1
    if any(k in sport_lower for k in ("f1", "formula 1", "formula1", "grand prix", "racing")):
        data = _f1_aggregator.get_info(query_raw)
        guidance = "Review the Formula 1 schedule/standings and inform the user. Remind them session times are in UTC."
    # 3. Soccer (EPL, UCL, La Liga, Serie A, Bundesliga, MLS, etc.)
    elif any(k in sport_lower for k in ("soccer", "football", "epl", "premier", "ucl", "champions", "laliga", "seriea", "bundesliga", "mls", "ligue1")) or any(k in query_lower for k in ("epl", "premier league", "champions league", "la liga", "serie a", "bundesliga", "mls")):
        data = _soccer_aggregator.get_info(query_raw if query_raw != "schedule" else sport_raw)
        guidance = "Present the soccer matches/standings clearly. Highlight live matches, scores, and goal scorers."
    # 4. NBA Basketball
    elif any(k in sport_lower for k in ("nba", "basketball", "hoops")):
        data = _nba_aggregator.get_info(query_raw)
        guidance = "Present the NBA games/standings clearly. Include scores, status, and quarter linescores if available."
    # 5. NHL Hockey
    elif any(k in sport_lower for k in ("nhl", "hockey", "ice hockey")):
        data = _hockey_aggregator.get_info(query_raw)
        guidance = "Present the NHL games/standings clearly. Include scores, status, and period linescores if available."
    # Fallback to soccer or web search if generic
    elif any(club in combined for club in _soccer_aggregator.CLUB_LEAGUE_HINTS):
        data = _soccer_aggregator.get_info(combined)
        guidance = "Present the match or table info cleanly for the requested team."
    else:
        return (
            f"Currently supported sports in skill_sports_info:\n"
            f"- Formula 1 ('f1'): race calendars, session times, driver/constructor standings\n"
            f"- Soccer ('soccer', 'epl', 'ucl', 'laliga', 'seriea', 'bundesliga', 'mls'): live scores, goal details, standings\n"
            f"- NBA Basketball ('nba'): live scores, quarter linescores, conference standings\n"
            f"- NHL Hockey ('nhl'): live scores, period linescores, standings\n"
            f"- Betting Odds ('odds'): moneylines and match odds across esports and sports\n\n"
            f"For other sports or niche leagues, please use `search_web`."
        )

    output = data
    output += "\n=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
    output += f"{guidance} Keep your response formatted cleanly with bold headers, bullet points, and concise details.\n"
    return output

