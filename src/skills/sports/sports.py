from typing import Optional
from datetime import datetime
from src.skills.sports.aggregators import (
    F1Aggregator,
    SoccerAggregator,
    NbaAggregator,
    HockeyAggregator,
    OddsAggregator,
)
from src.skills.sports.csgo import skill_csgo_esports

_f1_aggregator = F1Aggregator()
_soccer_aggregator = SoccerAggregator()
_nba_aggregator = NbaAggregator()
_hockey_aggregator = HockeyAggregator()
_odds_aggregator = OddsAggregator()

CS_KEYWORDS = {
    "cs", "cs2", "csgo", "counter-strike", "counter strike", "esports", "e-sports",
    "falcons", "nip", "ninjas in pyjamas", "vitality", "spirit", "team spirit",
    "navi", "natus vincere", "furia", "mouz", "faze", "g2", "virtus.pro",
    "astralis", "complexity", "the mongolz", "mongolz", "heroic", "eternal fire",
    "thunderpick", "blast", "pgl", "iem", "esl"
}

def skill_sports_info(sport: str, query: Optional[str] = "schedule") -> str:
    """
    Fetch live sports and esports information including match scores, goal scorers, quarter/period linescores,
    upcoming schedules, tournaments, betting odds, and championship standings.

    Supported sports:
    - 'cs' / 'cs2' / 'esports' / 'counter-strike': S-Tier CS tournaments, upcoming matches (e.g. Falcons, NIP), live streams, HLTV rankings, and betting odds.
    - 'f1' / 'formula 1': Grand Prix schedule, session times (FP1-3, Qual, Race), results, driver & constructor standings.
    - 'soccer' / 'football' / 'epl' / 'ucl' / 'laliga' / 'seriea' / 'bundesliga' / 'mls': Live matches, scores, goal scorers, fixtures, and standings.
    - 'nba' / 'basketball': Live scores, quarter linescores, today's schedule, and Eastern/Western conference standings.
    - 'nhl' / 'hockey': Live scores, period linescores, today's matchups, and conference standings.
    - 'odds' / 'betting': Live betting odds, moneylines, and match favorites across esports and sports.

    Args:
        sport: The sport to look up (e.g. 'cs', 'f1', 'soccer', 'nba', 'nhl', 'epl', 'ucl', 'odds').
        query: What to fetch ('schedule', 'scores', 'standings', 'results', team name, or date like 'yesterday'/'tomorrow').
    """
    sport_raw = (sport or "").strip()
    query_raw = (query or "schedule").strip()
    print(f"🏟️ [Executing Sports Info Skill] Sport: {sport_raw}, Query: {query_raw}")

    sport_lower = sport_raw.lower()
    query_lower = query_raw.lower()
    combined = f"{sport_lower} {query_lower}"

    # 1. Counter-Strike / Esports Check (handle CS first if matched in sport or query)
    is_cs = (
        sport_lower in ("cs", "cs2", "csgo", "counter-strike", "counter strike", "esports") or
        any(k in sport_lower for k in CS_KEYWORDS) or
        any(k in query_lower for k in CS_KEYWORDS)
    )
    if is_cs:
        if "odd" in query_lower or "bet" in query_lower:
            return _odds_aggregator.get_odds(query_raw, sport="cs2")
        elif any(k in query_lower for k in ("tourn", "schedule", "next", "upcoming", "event")):
            return skill_csgo_esports(query_type="tournaments")
        elif any(k in query_lower for k in ("rank", "top", "leader", "standings")):
            return skill_csgo_esports(query_type="rankings")
        elif any(k in query_lower for k in ("live", "stream", "broadcast")):
            return skill_csgo_esports(query_type="live_streams")
        else:
            return skill_csgo_esports(query_type="all", matchup=query_raw if query_raw != "schedule" else None)

    # 2. Betting odds check for traditional sports
    if "odd" in query_lower or "bet" in query_lower or sport_lower in ("odds", "betting"):
        return _odds_aggregator.get_odds(query_raw, sport=sport_lower)

    # 3. Formula 1
    if any(k in sport_lower for k in ("f1", "formula 1", "formula1", "grand prix", "racing")):
        data = _f1_aggregator.get_info(combined)
        guidance = "Review the Formula 1 schedule/standings and inform the user. Remind them session times are in UTC."
    # 4. Soccer (EPL, UCL, La Liga, Serie A, Bundesliga, MLS, etc.)
    elif any(k in sport_lower for k in ("soccer", "football", "epl", "premier", "ucl", "champions", "laliga", "seriea", "bundesliga", "mls", "ligue1")) or any(k in query_lower for k in ("epl", "premier league", "champions league", "la liga", "serie a", "bundesliga", "mls")):
        data = _soccer_aggregator.get_info(combined)
        guidance = "Present the soccer matches/standings clearly. Highlight live matches, scores, and goal scorers."
    # 5. NBA Basketball
    elif any(k in sport_lower for k in ("nba", "basketball", "hoops")):
        data = _nba_aggregator.get_info(combined)
        guidance = "Present the NBA games/standings clearly. Include scores, status, and quarter linescores if available."
    # 6. NHL Hockey
    elif any(k in sport_lower for k in ("nhl", "hockey", "ice hockey")):
        data = _hockey_aggregator.get_info(combined)
        guidance = "Present the NHL games/standings clearly. Include scores, status, and period linescores if available."
    # Fallback to soccer if club matches
    elif any(club in combined for club in _soccer_aggregator.CLUB_LEAGUE_HINTS):
        data = _soccer_aggregator.get_info(combined)
        guidance = "Present the match or table info cleanly for the requested team."
    else:
        return (
            f"Currently supported sports in skill_sports_info:\n"
            f"- Counter-Strike ('cs', 'cs2', 'esports'): tournaments, matches (Falcons, NIP, etc.), world rankings, and odds\n"
            f"- Formula 1 ('f1'): race calendars, session times, driver/constructor standings\n"
            f"- Soccer ('soccer', 'epl', 'ucl', 'laliga', 'seriea', 'bundesliga', 'mls'): live scores, goal details, standings\n"
            f"- NBA Basketball ('nba'): live scores, quarter linescores, conference standings\n"
            f"- NHL Hockey ('nhl'): live scores, period linescores, standings\n"
            f"- Betting Odds ('odds'): moneylines and match odds across esports and sports\n\n"
            f"For other sports or niche leagues, please use `search_web`."
        )

    now = datetime.now()
    today_str = now.strftime("%A, %B %d, %Y")

    output = f"📅 CURRENT REFERENCE DATE: {today_str}\n\n" + data
    output += "\n=== CRITICAL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
    output += f"1. TODAY'S DATE: {today_str}. All relative dates ('today', 'tomorrow', 'yesterday', 'this week', 'next week') MUST anchor on this date.\n"
    output += f"2. ALWAYS use the exact day of the week and date specified in the data (e.g. 'Wednesday, October 14, 2026'). NEVER guess, extrapolate, or alter calendar days of the week.\n"
    output += f"3. {guidance} Keep your response formatted cleanly with bold headers, bullet points, and concise details.\n"
    return output


