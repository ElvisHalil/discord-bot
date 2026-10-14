import re
from typing import Optional
from datetime import datetime
from src.skills.sports.aggregators import (
    F1Aggregator,
    SoccerAggregator,
    NbaAggregator,
    HockeyAggregator,
    OddsAggregator,
    UniversalEspnAggregator,
)
from src.skills.sports.aggregators.espn_utils import espn_search_auto_discover
from src.skills.sports.csgo import skill_csgo_esports

_f1_aggregator = F1Aggregator()
_soccer_aggregator = SoccerAggregator()
_nba_aggregator = NbaAggregator()
_hockey_aggregator = HockeyAggregator()
_odds_aggregator = OddsAggregator()
_universal_aggregator = UniversalEspnAggregator()

CS_KEYWORDS = {
    "cs", "cs2", "csgo", "counter-strike", "counter strike", "esports", "e-sports",
    "falcons", "nip", "ninjas in pyjamas", "vitality", "spirit", "team spirit",
    "navi", "natus vincere", "furia", "mouz", "faze", "g2", "virtus.pro",
    "astralis", "complexity", "the mongolz", "mongolz", "heroic", "eternal fire",
    "thunderpick", "blast", "pgl", "iem", "esl"
}


def skill_sports_info(
    sport: Optional[str] = None,
    query: Optional[str] = None,
    team: Optional[str] = None,
    player: Optional[str] = None,
) -> str:
    """
    Fetch live sports and esports information across ALL major sports via ESPN & esports APIs:
    match scores, goal scorers, yellow/red cards, possession & match stats, starting lineups/substitutions,
    player season stats, squad rosters, injury reports, quarter/period/inning linescores, upcoming schedules,
    tournaments, betting odds, and standings.
    Do NOT use this tool for AI models, technology, coding, general news, or non-sports topics (use search_web instead).

    Supported sports (plus automatic team/player discovery across all ESPN sports):
    - 'soccer' / 'epl' / 'ucl' / 'laliga' / 'seriea' / 'bundesliga' / 'ligue1' / 'mls': Matches, scores, goals, assists, yellow/red cards, possession/shots stats, lineups, substitutions, player stats, rosters, top scorers, injuries, and standings.
    - 'nba' / 'wnba' / 'ncaab' / 'basketball': Live scores, quarter linescores, game leaders, team schedules, rosters, player stats, injury reports, and standings.
    - 'nhl' / 'hockey': Live scores, period linescores, game leaders, team schedules, rosters, player stats, injury reports, and standings.
    - 'nfl' / 'ncaaf' / 'football': NFL & College Football scores, schedules, rosters, player stats, injuries, and standings.
    - 'mlb' / 'baseball': MLB scores, inning linescores, schedules, rosters, player stats, injuries, and standings.
    - 'ufc' / 'mma' / 'tennis' / 'atp' / 'wta' / 'pga' / 'golf': Fight cards, tournament scoreboards, and athlete profiles.
    - 'f1' / 'formula 1': Grand Prix schedule, session times (FP1-3, Qual, Race), results, driver & constructor standings.
    - 'cs' / 'cs2' / 'esports': S-Tier CS tournaments, upcoming matches, live streams, HLTV rankings, player stats, and betting odds.
    - 'odds' / 'betting': Live betting odds, moneylines, and match favorites across esports and sports.

    Args:
        sport: The sport or league to look up (e.g. 'soccer', 'bundesliga', 'epl', 'ucl', 'nba', 'nhl', 'nfl', 'mlb', 'ufc', 'f1', 'cs', 'odds').
        query: What to fetch ('schedule', 'scores', 'standings', 'results', 'roster', 'injuries', 'lineups', 'player stats', 'top scorers', 'cards', or full question).
        team: Optional specific team or club name (e.g. 'Bayern Munich', 'Arsenal', 'Kansas City Chiefs', 'Lakers').
        player: Optional specific player or athlete name (e.g. 'Harry Kane', 'Erling Haaland', 'Patrick Mahomes', 'Shohei Ohtani').
    """
    sport_raw = (sport or "").strip()
    query_raw = (query or "").strip()
    team_raw = (team or "").strip()
    player_raw = (player or "").strip()

    # If sport is a sentence or long prompt, treat it as query content
    if len(sport_raw.split()) > 2 and query_raw in ("schedule", ""):
        query_raw = sport_raw
        sport_raw = ""

    parts = [p for p in (sport_raw, team_raw, player_raw, query_raw) if p]
    combined = " ".join(parts).strip().lower()
    if not combined:
        sport_raw = "soccer"
        query_raw = "schedule"
        combined = "soccer schedule"

    # Explicit guardrail: Reject AI models, tech, coding, and non-sports queries mistakenly routed here
    if re.search(r'\b(?:gemini|opus|claude|chatgpt|gpt-[0-9a-z]+|deepseek|qwen|llama|llms?|benchmarks?|antigravity|coding|python|javascript|nvidia|rtx|intel|amd)\b', combined):
        return (
            f"⚠️ Query '{combined}' is not a sports query.\n"
            f"`skill_sports_info` ONLY supports sports and esports.\n"
            f"For AI models, benchmarks, technology, programming, news, or general knowledge: please use `search_web` instead."
        )

    # 1. Counter-Strike / Esports Check (guard against NFL "Atlanta Falcons" and substring false positives like 'esl' in 'bundesliga' or 'cs' in 'celtics')
    is_nfl_falcons = "atlanta" in combined or sport_raw.lower() in ("nfl", "football", "american football")
    is_cs = not is_nfl_falcons and (
        sport_raw.lower() in ("cs", "cs2", "csgo", "counter-strike", "counter strike", "esports") or
        any(bool(re.search(rf'\b{re.escape(k)}\b', combined)) for k in CS_KEYWORDS)
    )
    if is_cs:
        sport_raw = "cs"
        target_q = " ".join(p for p in (team_raw, player_raw, query_raw) if p).strip() or combined
        named_event = target_q if any(t in combined for t in ("thunderpick", "pgl", "blast", "iem", "esl", "major", "bucharest", "chengdu", "atlanta")) else None
        if re.search(r'\b(?:odds?|bet|bets|betting|prop|props|moneyline)\b', combined):
            return _odds_aggregator.get_odds(target_q, sport="cs2")
        elif re.search(r'\b(?:stats|statistics|rating|kdr|kd)\b', combined):
            return skill_csgo_esports(query_type="player_stats", player_url=player_raw or team_raw or target_q)
        elif any(k in combined for k in ("roster", "player", "lineup", "group", "bracket", "teams")):
            return skill_csgo_esports(query_type="event_info", event_page=named_event or (target_q if target_q not in ("schedule", "") else None))
        elif any(k in combined for k in ("tourn", "schedule", "next", "upcoming", "event", "match", "matches", "time", "today", "tomorrow", "when", "play")):
            return skill_csgo_esports(query_type="tournaments", event_page=named_event)
        elif any(k in combined for k in ("rank", "top", "leader", "standings")):
            return skill_csgo_esports(query_type="rankings")
        elif any(k in combined for k in ("live", "stream", "broadcast")):
            return skill_csgo_esports(query_type="live_streams")
        else:
            is_team_matchup = " vs " in combined or sum(1 for t in _odds_aggregator.CS_TEAMS if bool(re.search(rf'\b{re.escape(t)}\b', combined))) >= 2
            return skill_csgo_esports(
                query_type="all",
                event_page=named_event,
                matchup=target_q if is_team_matchup else None,
            )

    # 2. Betting odds check for traditional sports (use word boundaries so 'betis' or 'better' do not match 'bet')
    if re.search(r'\b(?:odds?|bet|bets|betting|moneyline)\b', combined) or sport_raw.lower() in ("odds", "betting"):
        return _odds_aggregator.get_odds(combined, sport=sport_raw.lower())

    # 3. Formula 1 Check
    is_f1 = (
        sport_raw.lower() in ("f1", "formula 1", "formula1", "grand prix", "racing") or
        bool(re.search(r'\b(?:f1|formula\s*1|formula1|grand\s*prix|constructors?|drivers?\s*championship|constructors?\s*championship|verstappen|hamilton|leclerc|norris|piastri|antonelli)\b', combined)) or
        any(k in combined for k in ("f1 race", "f1 standings", "f1 calendar"))
    )
    if is_f1:
        sport_raw = "f1"
        data = _f1_aggregator.get_info(combined)
        guidance = "Review the Formula 1 schedule/standings and inform the user. Remind them session times are in UTC."

    # 4. NBA Basketball Check (excluding WNBA / NCAAB which route to UniversalEspnAggregator)
    elif (
        not any(w in combined for w in ("wnba", "ncaab", "college basketball", "march madness")) and (
            sport_raw.lower() in ("nba", "basketball", "hoops") or
            bool(re.search(r'\b(?:nba|basketball|hoops|lakers|celtics|warriors|knicks|sixers|76ers|bucks|cavaliers|cavs|nuggets|mavericks|mavs|suns|clippers|timberwolves|pelicans|rockets|grizzlies|blazers|pacers|pistons|raptors|wizards|hornets|hawks|bulls)\b', combined))
        )
    ):
        sport_raw = "nba"
        data = _nba_aggregator.get_info(combined)
        guidance = "Present the NBA games, leaders, injuries, rosters, or standings clearly. Include scores, status, and quarter linescores if available."

    # 5. NHL Hockey Check
    elif (
        sport_raw.lower() in ("nhl", "hockey", "ice hockey") or
        bool(re.search(r'\b(?:nhl|hockey|ice\s*hockey|stanley\s*cup|bruins|maple\s*leafs|leafs|canadiens|habs|red\s*wings|lightning|sabres|senators|islanders|devils|penguins|flyers|capitals|hurricanes|blue\s*jackets|oilers|flames|canucks|golden\s*knights|kraken|avalanche|predators|blackhawks|sharks|ducks)\b', combined))
    ):
        sport_raw = "nhl"
        data = _hockey_aggregator.get_info(combined)
        guidance = "Present the NHL games, leaders, injuries, rosters, or standings clearly. Include scores, status, and period linescores if available."

    # 6. Explicit Universal ESPN Sports (NFL, NCAAF, MLB, WNBA, NCAAB, UFC/MMA, Tennis, Golf)
    elif (
        sport_raw.lower() in ("nfl", "ncaaf", "cfb", "mlb", "baseball", "wnba", "ncaab", "ufc", "mma", "tennis", "atp", "wta", "pga", "golf") or
        any(
            alias in combined
            for reg in _universal_aggregator.SPORT_LEAGUE_REGISTRY.values()
            for alias in reg["aliases"]
        )
    ):
        sport_raw = sport_raw or "espn"
        data = _universal_aggregator.get_info(combined)
        guidance = "Present the sports scores, schedules, rosters, player stats, injuries, or standings clearly."

    # 7. Soccer & Universal Auto-Discovery Fallback
    else:
        soccer_keywords = (
            "soccer", "football", "epl", "premier", "ucl", "champions",
            "laliga", "la liga", "seriea", "serie a", "bundesliga", "mls",
            "ligue1", "ligue 1", "pitch", "relegation", "golden boot", "starting xi"
        )
        general_match_keywords = (
            "match", "matches", "fixture", "fixtures", "game", "games",
            "score", "scores", "schedule", "standings", "table", "results",
            "outcome", "outcomes", "today", "tomorrow", "yesterday", "this week", "next week",
            "injuries", "injured", "injury", "roster", "squad", "lineup", "formation",
            "cards", "yellow card", "red card", "scorers", "assists", "possession", "boxscore", "player stats"
        )

        is_explicit_soccer = (
            sport_raw.lower() in ("soccer", "football", "epl", "ucl", "laliga", "seriea", "bundesliga", "mls", "ligue1") or
            any(k in combined for k in soccer_keywords) or
            any(club in combined for club in _soccer_aggregator.CLUB_LEAGUE_HINTS)
        )

        if is_explicit_soccer:
            sport_raw = "soccer"
            data = _soccer_aggregator.get_info(combined)
            guidance = "Present the soccer matches, scores, goal scorers, cards, possession/shots stats, lineups, rosters, injuries, or standings clearly."
        else:
            # Try ESPN Universal Search v2 Auto-Discovery for any team, player, or league worldwide
            disc = espn_search_auto_discover(combined)
            if disc:
                d_sport = disc.get("sport", "")
                d_league = disc.get("league", "")
                if d_sport == "soccer":
                    sport_raw = "soccer"
                    data = _soccer_aggregator.get_info(combined)
                    guidance = "Present the soccer matches, scores, goal scorers, cards, possession/shots stats, lineups, rosters, injuries, or standings clearly."
                elif d_sport == "basketball" and d_league == "nba":
                    sport_raw = "nba"
                    data = _nba_aggregator.get_info(combined)
                    guidance = "Present the NBA games, leaders, injuries, rosters, or standings clearly."
                elif d_sport == "hockey" and d_league == "nhl":
                    sport_raw = "nhl"
                    data = _hockey_aggregator.get_info(combined)
                    guidance = "Present the NHL games, leaders, injuries, rosters, or standings clearly."
                else:
                    sport_raw = f"{d_sport}/{d_league}"
                    data = _universal_aggregator.get_info(combined)
                    guidance = "Present the sports scores, schedules, rosters, player stats, injuries, or standings clearly."
            elif (
                sport_raw.lower() in ("sports", "sport", "general", "all") or
                (sport_raw.lower() == "" and any(k in combined for k in general_match_keywords)) or
                bool(re.search(r'\b(?:sports?)\b', combined))
            ):
                sport_raw = "soccer"
                data = _soccer_aggregator.get_info(combined)
                guidance = "Present the soccer matches, scores, goal scorers, cards, possession/shots stats, lineups, rosters, injuries, or standings clearly."
            else:
                return (
                    f"⚠️ Query '{combined}' is not recognized as a sport, team, or athlete.\n"
                    f"`skill_sports_info` ONLY supports sports and esports.\n"
                    f"For AI models, technology, coding, general news, or other topics: please use `search_web` instead."
                )

    print(f"🏟️ [Executing Sports Info Skill] Sport: {sport_raw}, Query: {combined}")

    now = datetime.now()
    today_str = now.strftime("%A, %B %d, %Y")

    output = f"📅 CURRENT REFERENCE DATE: {today_str}\n\n" + data
    output += "\n=== CRITICAL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
    output += f"1. TODAY'S DATE: {today_str}. All relative dates ('today', 'tomorrow', 'yesterday', 'this week', 'next week') MUST anchor on this date.\n"
    output += f"2. ALWAYS use the exact day of the week and date specified in the data (e.g. 'Wednesday, October 14, 2026'). NEVER guess, extrapolate, or alter calendar days of the week.\n"
    output += "3. If a match is marked 🔴 LIVE NOW / CURRENTLY PLAYING RIGHT NOW, ALWAYS report that live match first (including its current score, minute clock, goal scorers, and match stats) before mentioning future fixtures.\n"
    output += f"4. {guidance} Keep your response formatted cleanly with bold headers, bullet points, and concise details.\n"
    return output
