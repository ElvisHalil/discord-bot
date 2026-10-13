import re
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

def skill_sports_info(sport: Optional[str] = None, query: Optional[str] = None) -> str:
    """
    Fetch live sports and esports information including match scores, goal scorers, quarter/period linescores,
    upcoming schedules, tournaments, betting odds, and championship standings.
    ONLY for sports: Soccer, Formula 1, NBA, NHL, CS/Esports, and Sports Betting.
    Do NOT use this tool for AI models, technology, coding, general news, or non-sports topics (use search_web instead).

    Supported sports:
    - 'cs' / 'cs2' / 'esports' / 'counter-strike': S-Tier CS tournaments, upcoming matches (e.g. Falcons, NIP), live streams, HLTV rankings, and betting odds.
    - 'f1' / 'formula 1': Grand Prix schedule, session times (FP1-3, Qual, Race), results, driver & constructor standings.
    - 'soccer' / 'football' / 'epl' / 'ucl' / 'laliga' / 'seriea' / 'bundesliga' / 'mls': Live matches, scores, goal scorers, fixtures, and standings.
    - 'nba' / 'basketball': Live scores, quarter linescores, today's schedule, and Eastern/Western conference standings.
    - 'nhl' / 'hockey': Live scores, period linescores, today's matchups, and conference standings.
    - 'odds' / 'betting': Live betting odds, moneylines, and match favorites across esports and sports.

    Args:
        sport: The sport to look up (e.g. 'cs', 'f1', 'soccer', 'nba', 'nhl', 'epl', 'ucl', 'odds').
        query: What to fetch ('schedule', 'scores', 'standings', 'results', team name, date, or query like 'previous matches and outcomes').
    """
    sport_raw = (sport or "").strip()
    query_raw = (query or "").strip()

    # If sport is a sentence or long prompt, treat it as query content
    if len(sport_raw.split()) > 2 and query_raw in ("schedule", ""):
        query_raw = sport_raw
        sport_raw = ""

    combined = f"{sport_raw} {query_raw}".strip().lower()
    if not combined:
        sport_raw = "soccer"
        query_raw = "schedule"
        combined = "soccer schedule"

    # Explicit guardrail: Reject AI models, tech, coding, and non-sports queries mistakenly routed here
    if re.search(r'\b(?:gemini|opus|claude|chatgpt|gpt-[0-9a-z]+|deepseek|qwen|llama|llms?|benchmarks?|antigravity|coding|python|javascript|nvidia|rtx|intel|amd)\b', combined):
        return (
            f"⚠️ Query '{query_raw or sport_raw}' is not a sports query.\n"
            f"`skill_sports_info` ONLY supports sports and esports (Soccer, Formula 1, NBA, NHL, Counter-Strike, Betting Odds).\n"
            f"For AI models, benchmarks, technology, programming, news, or general knowledge: please use `search_web` instead."
        )

    # 1. Counter-Strike / Esports Check (handle CS first if matched in sport or query)
    is_cs = (
        sport_raw.lower() in ("cs", "cs2", "csgo", "counter-strike", "counter strike", "esports") or
        any(k in combined for k in CS_KEYWORDS) or
        bool(re.search(r'\b(?:cs|cs2|csgo|counter-strike)\b', combined))
    )
    if is_cs:
        sport_raw = "cs"
        if "odd" in combined or "bet" in combined:
            return _odds_aggregator.get_odds(query_raw or combined, sport="cs2")
        elif any(k in combined for k in ("tourn", "schedule", "next", "upcoming", "event")):
            return skill_csgo_esports(query_type="tournaments")
        elif any(k in combined for k in ("rank", "top", "leader", "standings")):
            return skill_csgo_esports(query_type="rankings")
        elif any(k in combined for k in ("live", "stream", "broadcast")):
            return skill_csgo_esports(query_type="live_streams")
        else:
            return skill_csgo_esports(query_type="all", matchup=query_raw if query_raw not in ("schedule", "") else None)

    # 2. Betting odds check for traditional sports
    if "odd" in combined or "bet" in combined or sport_raw.lower() in ("odds", "betting"):
        return _odds_aggregator.get_odds(query_raw or combined, sport=sport_raw.lower())

    # 3. Formula 1 Check
    is_f1 = (
        sport_raw.lower() in ("f1", "formula 1", "formula1", "grand prix", "racing") or
        bool(re.search(r'\b(?:f1|formula\s*1|formula1|grand\s*prix|constructors?|drivers?\s*championship|constructors?\s*championship|verstappen|hamilton|leclerc|norris|piastri|antonelli)\b', combined)) or
        any(k in combined for k in ("f1 race", "f1 standings", "f1 calendar"))
    )
    if is_f1:
        sport_raw = "f1"
        data = _f1_aggregator.get_info(query_raw or combined)
        guidance = "Review the Formula 1 schedule/standings and inform the user. Remind them session times are in UTC."

    # 4. NBA Basketball Check
    elif (
        sport_raw.lower() in ("nba", "basketball", "hoops") or
        bool(re.search(r'\b(?:nba|basketball|hoops|lakers|celtics|warriors)\b', combined))
    ):
        sport_raw = "nba"
        data = _nba_aggregator.get_info(query_raw or combined)
        guidance = "Present the NBA games/standings clearly. Include scores, status, and quarter linescores if available."

    # 5. NHL Hockey Check
    elif (
        sport_raw.lower() in ("nhl", "hockey", "ice hockey") or
        bool(re.search(r'\b(?:nhl|hockey|ice\s*hockey|stanley\s*cup)\b', combined))
    ):
        sport_raw = "nhl"
        data = _hockey_aggregator.get_info(query_raw or combined)
        guidance = "Present the NHL games/standings clearly. Include scores, status, and period linescores if available."

    # 6. Soccer Check (or general sports if sports/match terms present)
    else:
        soccer_keywords = (
            "soccer", "football", "epl", "premier", "ucl", "champions",
            "laliga", "la liga", "seriea", "serie a", "bundesliga", "mls",
            "ligue1", "ligue 1", "pitch", "relegation"
        )
        general_match_keywords = (
            "match", "matches", "fixture", "fixtures", "game", "games",
            "score", "scores", "schedule", "standings", "table", "results",
            "outcome", "outcomes", "today", "tomorrow", "yesterday", "this week", "next week"
        )

        is_soccer = (
            sport_raw.lower() in ("soccer", "football", "epl", "ucl", "laliga", "seriea", "bundesliga", "mls", "ligue1") or
            any(k in combined for k in soccer_keywords) or
            any(club in combined for club in _soccer_aggregator.CLUB_LEAGUE_HINTS)
        )

        is_generic_sports = (
            sport_raw.lower() in ("sports", "sport", "general", "all") or
            (sport_raw.lower() == "" and any(k in combined for k in general_match_keywords)) or
            bool(re.search(r'\b(?:sports?)\b', combined))
        )

        if is_soccer or is_generic_sports:
            sport_raw = "soccer"
            data = _soccer_aggregator.get_info(query_raw or combined)
            guidance = "Present the soccer matches/standings clearly. Highlight live matches, scores, and goal scorers."
        else:
            return (
                f"⚠️ Query '{query_raw or sport_raw}' is not recognized as a sport, team, or match.\n"
                f"`skill_sports_info` ONLY supports sports and esports (Soccer, Formula 1, NBA, NHL, Counter-Strike, Betting Odds).\n"
                f"For AI models, technology, coding, general news, or other topics: please use `search_web` instead."
            )

    print(f"🏟️ [Executing Sports Info Skill] Sport: {sport_raw}, Query: {query_raw or combined}")

    now = datetime.now()
    today_str = now.strftime("%A, %B %d, %Y")

    output = f"📅 CURRENT REFERENCE DATE: {today_str}\n\n" + data
    output += "\n=== CRITICAL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
    output += f"1. TODAY'S DATE: {today_str}. All relative dates ('today', 'tomorrow', 'yesterday', 'this week', 'next week') MUST anchor on this date.\n"
    output += f"2. ALWAYS use the exact day of the week and date specified in the data (e.g. 'Wednesday, October 14, 2026'). NEVER guess, extrapolate, or alter calendar days of the week.\n"
    output += f"3. {guidance} Keep your response formatted cleanly with bold headers, bullet points, and concise details.\n"
    return output


