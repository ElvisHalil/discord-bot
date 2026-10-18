import re
from typing import Optional

from src.skills.sports.aggregators import (
    EslLiveAggregator,
    F1Aggregator,
    HltvAggregator,
    HockeyAggregator,
    LiquipediaAggregator,
    NbaAggregator,
    OddsAggregator,
    SoccerAggregator,
    UniversalEspnAggregator,
)
from src.skills.sports.aggregators.espn_utils import (
    espn_search_auto_discover,
)
from src.utils.workers import map_in_workers, submit_to_worker
from src.skills.sports.catalogs import CS_KEYWORDS
from src.skills.sports.enums import CsQueryType, SportId, TrackerAction
from src.skills.sports.templates import (
    build_cs_llm_instructions,
    build_odds_llm_instructions,
    build_sports_llm_instructions,
)
from src.skills.sports.tracker import (
    get_channel_last_match_query,
    skill_sports_tracker,
)
from src.utils.domain_vocab import has_non_sports_track_signal
from src.utils.timezones import get_local_now

_esl_aggregator = EslLiveAggregator()
_liquipedia_aggregator = LiquipediaAggregator()
_hltv_aggregator = HltvAggregator()
_f1_aggregator = F1Aggregator()
_soccer_aggregator = SoccerAggregator()
_nba_aggregator = NbaAggregator()
_hockey_aggregator = HockeyAggregator()
_odds_aggregator = OddsAggregator()
_universal_aggregator = UniversalEspnAggregator()

_TECH_RE = re.compile(
    r'\b(?:gemini|opus|claude|chatgpt|gpt-[0-9a-z]+|deepseek|qwen|llama|llms?|benchmarks?|antigravity|coding|python|javascript|nvidia|rtx|intel|amd)\b',
    re.IGNORECASE,
)


def skill_sports_esports(
    query_type: Optional[str] = CsQueryType.ALL,
    event_page: Optional[str] = None,
    player_url: Optional[str] = None,
    matchup: Optional[str] = None,
) -> str:
    """
    Use this skill to fetch Counter-Strike (CS) esports information.
    query_type can be:
      - 'tournaments': Gets upcoming and ongoing S-Tier tournaments from Liquipedia.
      - 'event_info': Gets detailed info about a specific event, including CURRENT MATCHES and full TEAM ROSTERS (requires event_page).
      - 'rankings': Gets the current world team rankings from HLTV, including team rosters.
      - 'player_stats': Gets stats for a specific player (requires player_url).
      - 'live_streams': Checks if ESLCS, BLASTPremier, or thunderpickesports is currently streaming a live match on YouTube.
      - 'odds': Gets live betting odds, moneylines, and win probabilities for a matchup (pass matchup or event_page).
      - 'all': Fetches 'tournaments', 'rankings', and 'live_streams'.

    Args:
        query_type: One of 'tournaments', 'event_info', 'rankings', 'player_stats', 'live_streams', 'odds', 'all' (default).
        event_page: Liquipedia event page, required for query_type='event_info'.
        player_url: Player profile URL, required for query_type='player_stats'.
        matchup: Team matchup for betting odds (e.g. 'Falcons vs Ninjas in Pyjamas'), used for query_type='odds'.
    """
    print(f"🎮 [Executing CS Esports Skill] Type: {query_type}, Matchup: {matchup}")
    output = "=== COUNTER-STRIKE ESPORTS DATA ===\n\n"

    # Normalize query_type to prevent empty data when models pass natural language
    q_type = (query_type or CsQueryType.ALL).lower().strip()
    if any(w in q_type for w in ("odd", "bet", "moneyline", "payout", "win", "prop", "line")):
        q_type = CsQueryType.ODDS
    elif any(w in q_type for w in ("stat", "rating", "kdr", "kd")) or q_type == CsQueryType.PLAYER_STATS or player_url:
        q_type = CsQueryType.PLAYER_STATS
    elif any(w in q_type for w in ("event", "roster", "lineup", "group", "bracket", "table")) or ("player" in q_type and not player_url):
        q_type = CsQueryType.EVENT_INFO
    elif any(w in q_type for w in ("live", "stream", "broadcast")):
        q_type = CsQueryType.LIVE_STREAMS
    elif any(w in q_type for w in ("tourn", "schedule", "calendar", "match", "matches", "time", "today", "tomorrow", "upcoming", "next")):
        q_type = CsQueryType.TOURNAMENTS
    elif any(w in q_type for w in ("rank", "top", "leader", "standings")):
        q_type = CsQueryType.RANKINGS
    elif q_type not in tuple(CsQueryType):
        if any(team in q_type for team in _odds_aggregator.CS_TEAMS):
            q_type = CsQueryType.ODDS
            if not matchup:
                matchup = query_type
        else:
            q_type = CsQueryType.ALL

    if q_type == CsQueryType.ALL:
        fut_esl = submit_to_worker(_esl_aggregator.get_live_matches)
        if event_page:
            fut_tourn = submit_to_worker(_liquipedia_aggregator.get_s_tier_tournaments, False)
        else:
            fut_tourn = submit_to_worker(_liquipedia_aggregator.get_s_tier_tournaments)
        fut_rank = submit_to_worker(_hltv_aggregator.get_rankings, 7)
        if event_page:
            fut_ev = submit_to_worker(_liquipedia_aggregator.get_event_info, event_page)
            output += fut_esl.result() + fut_tourn.result() + fut_rank.result() + fut_ev.result()
        else:
            output += fut_esl.result() + fut_tourn.result() + fut_rank.result()
    else:
        # 1. LIVE STREAMS
        if q_type == CsQueryType.LIVE_STREAMS:
            output += _esl_aggregator.get_live_matches()

        # 2. TOURNAMENTS (auto-includes active/next event details unless a specific event_page is requested below)
        if q_type == CsQueryType.TOURNAMENTS:
            if event_page:
                output += _liquipedia_aggregator.get_s_tier_tournaments(include_next_event_details=False)
            else:
                output += _liquipedia_aggregator.get_s_tier_tournaments()

        # 3. HLTV RANKINGS
        if q_type == CsQueryType.RANKINGS:
            output += _hltv_aggregator.get_rankings(limit=15)

        # 4. EVENT INFO (triggered when q_type == 'event_info' OR whenever event_page is provided for non-odds/non-stats queries)
        if q_type == CsQueryType.EVENT_INFO or (event_page and q_type not in (CsQueryType.ODDS, "betting", CsQueryType.PLAYER_STATS)):
            output += _liquipedia_aggregator.get_event_info(event_page)

    # 5. PLAYER / TEAM STATS
    if q_type == CsQueryType.PLAYER_STATS:
        stats_target = player_url or matchup or event_page or (query_type if q_type != (query_type or "").lower().strip() else None)
        output += _hltv_aggregator.get_player_stats(stats_target)

    # 6. BETTING ODDS / TEAM MATCHUP
    if q_type in (CsQueryType.ODDS, "betting") or (matchup and q_type not in (CsQueryType.PLAYER_STATS, CsQueryType.EVENT_INFO)):
        target = matchup or event_page or player_url or "Falcons vs Ninjas in Pyjamas"
        output += _odds_aggregator.get_odds(target, sport="cs2")

    output += build_cs_llm_instructions()
    return output


def skill_sports_odds(query: str, sport: Optional[str] = SportId.SPORTS) -> str:
    """
    Fetch current match betting odds, decimal payout multipliers, and implied win probabilities for sports, F1, and esports.

    Args:
        query: Matchup, team, league, event name, or 'tracked games' (e.g. 'Washington Capitals vs Montreal Canadiens', 'soccer odds', 'nhl odds', 'f1 odds', 'Real Madrid', 'Aurora vs 9z').
        sport: Sport category, default 'sports' (e.g. 'nhl', 'nba', 'nfl', 'soccer', 'ucl', 'epl', 'mlb', 'f1', 'cs2', 'esports').
    """
    print(f"🎲 [Executing Sports Odds Skill] Matchup: '{query}' (Sport: '{sport}')")
    odds_data = _odds_aggregator.get_odds(query, sport=sport)
    return odds_data + build_odds_llm_instructions()


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
        sport_raw = SportId.SOCCER
        query_raw = "schedule"
        combined = "soccer schedule"

    # Explicit guardrail: Reject AI models, tech, coding, and non-sports queries mistakenly routed here
    if _TECH_RE.search(combined):
        has_valid_sport_signal = (
            sport_raw.lower() in ("soccer", "epl", "ucl", "laliga", "seriea", "bundesliga", "ligue1", "mls", "nba", "nhl", "nfl", "mlb", "ufc", "f1", "cs", "cs2", "odds")
            or any(club in combined for club in _soccer_aggregator.CLUB_LEAGUE_HINTS)
            or any(bool(re.search(rf'\b{re.escape(k)}\b', combined)) for k in CS_KEYWORDS)
            or bool(re.search(r'\b(?:soccer|epl|ucl|laliga|bundesliga|serie\s*a|nba|nhl|hockey|nfl|mlb|ufc|f1|formula\s*1|lakers|celtics|warriors|oilers|flames|bruins|leafs)\b', combined))
        )
        if not has_valid_sport_signal:
            return (
                f"⚠️ Query '{combined}' is not a sports query.\n"
                f"`skill_sports_info` ONLY supports sports and esports.\n"
                f"For AI models, benchmarks, technology, programming, news, or general knowledge: please use `search_web` instead."
            )
        combined = _TECH_RE.sub(" ", combined)
        combined = re.sub(r"\s{2,}", " ", combined).strip()

    # 0. Live Match Tracking Check (if LLM calls skill_sports_info instead of skill_sports_tracker)
    if re.search(r'\b(?:untrack|stop\s+tracking)\b', combined):
        return skill_sports_tracker(action=TrackerAction.STOP, query=team_raw or query_raw or combined, sport=sport_raw)
    if (
        re.search(r'\b(?:track|tracking)\b', combined)
        and not re.search(r'\b(?:track\s+and\s+field|race\s+track|soundtrack)\b', combined)
        and not has_non_sports_track_signal(combined)
    ):
        return skill_sports_tracker(action=TrackerAction.START, query=team_raw or query_raw or combined, sport=sport_raw)

    # 0b. If query is a generic follow-up about "the game" / "last goal" / "how's it going" with no team entity,
    # inherit the channel's last inspected or actively tracked match.
    if not team_raw and not player_raw:
        has_known_entity = (
            any(club in combined for club in _soccer_aggregator.CLUB_LEAGUE_HINTS)
            or any(bool(re.search(rf'\b{re.escape(k)}\b', combined)) for k in CS_KEYWORDS)
            or bool(re.search(r'\b(?:f1|formula\s*1|nba|nhl|nfl|mlb|ufc|mma|epl|ucl|laliga|la\s+liga|serie\s*a|bundesliga|ligue\s*1|mls)\b', combined))
        )
        is_generic_match_followup = bool(
            re.search(
                r'\b(?:this\s+game|that\s+game|the\s+game|this\s+match|that\s+match|the\s+match|hows?\s+the\s+game|how\s+is\s+the\s+game|who\s+scored|last\s+goal|still\s+live|not\s+over|what\s+minute|whats?\s+the\s+score|whats?\s+the\s+status|status\s+of\s+(?:the|that|this)\s+(?:game|match))\b',
                combined,
            )
        )
        if not has_known_entity and is_generic_match_followup:
            last_q, last_s = get_channel_last_match_query()
            if last_q and last_q.lower() not in combined:
                combined = f"{last_q.lower()} {combined}".strip()
                if not sport_raw and last_s:
                    sport_raw = last_s

    has_odds_kw = bool(re.search(r'\b(?:odds?|bet|bets|betting|moneyline)\b', combined)) or sport_raw.lower() in ("odds", "betting")
    wants_games_and_odds = has_odds_kw and bool(
        re.search(
            r'\b(?:games?|matches|schedule|fixtures?|scores?|playing)\b.*\b(?:and|with|plus|also|&)\b.*\b(?:odds?|betting|lines?)\b|'
            r'\b(?:odds?|betting|lines?)\b.*\b(?:and|with|for\s+all)\b.*\b(?:games?|matches|schedule|fixtures?)\b',
            combined,
        )
    )

    def _scope_query_for_sport(q_text: str, target_sport: str) -> str:
        """Strip other sport names/clauses and 'and their odds' filler when splitting a multi-sport or games+odds query."""
        cleaned = q_text
        if wants_games_and_odds:
            cleaned = re.sub(r'\b(?:and\s+(?:their\s+)?|with\s+(?:their\s+)?)?(?:betting\s+)?(?:odds?|lines?|moneylines?)\b', ' ', cleaned, flags=re.IGNORECASE)
        _cs_words = ("cs", "cs2", "csgo", "counter-strike", "counter strike", "esports", "e-sports", "hltv", "spirit", "vitality", "navi", "faze", "mouz", "g2")
        _soccer_words = ("soccer", "epl", "premier league", "ucl", "champions league", "laliga", "la liga", "serie a", "bundesliga", "real madrid", "arsenal", "barcelona", "bayern", "chelsea", "liverpool")
        _nhl_words = ("nhl", "hockey", "ice hockey", "oilers", "flames", "bruins", "leafs", "maple leafs")
        _nba_words = ("nba", "basketball", "lakers", "celtics", "warriors", "knicks")
        _f1_words = ("f1", "formula 1", "grand prix")
        sport_vocab = {
            "soccer": _soccer_words,
            "nhl": _nhl_words,
            "nba": _nba_words,
            "f1": _f1_words,
            "cs": _cs_words,
        }
        other_sport_words = {
            "soccer": _nhl_words + _nba_words + _f1_words + _cs_words,
            "nhl": _soccer_words + _nba_words + _f1_words + _cs_words,
            "nba": _soccer_words + _nhl_words + _f1_words + _cs_words,
            "f1": _soccer_words + _nhl_words + _nba_words + _cs_words,
            "cs": _soccer_words + _nhl_words + _nba_words + _f1_words,
        }.get(target_sport, ())

        # If the prompt has multiple clauses ('cs rankings and when do the lakers play'),
        # drop clauses that only mention another sport so intent words like 'rankings' or 'play' don't bleed across sports,
        # while preserving shared day/time words ('today', 'tomorrow', 'yesterday', 'tonight') from list prompts.
        shared_time_words = [
            w for w in ("today", "tomorrow", "yesterday", "tonight")
            if re.search(rf'\b{w}\b', q_text, re.IGNORECASE)
        ]
        own_words = sport_vocab.get(target_sport, ())
        clauses = [c.strip() for c in re.split(r'\b(?:and\s+also|and|also|plus)\b|[,;]', cleaned, flags=re.IGNORECASE) if c and c.strip()]
        if len(clauses) >= 2 and other_sport_words:
            kept_clauses = []
            for c in clauses:
                has_own = any(bool(re.search(rf'\b{re.escape(w)}\b', c, re.IGNORECASE)) for w in own_words)
                has_other = any(bool(re.search(rf'\b{re.escape(w)}\b', c, re.IGNORECASE)) for w in other_sport_words)
                if has_own or not has_other:
                    kept_clauses.append(c)
            if kept_clauses:
                cleaned = " ".join(kept_clauses)

        for w in other_sport_words:
            cleaned = re.sub(rf'\b{re.escape(w)}\b', ' ', cleaned, flags=re.IGNORECASE)
        for tw in shared_time_words:
            if not re.search(rf'\b{tw}\b', cleaned, re.IGNORECASE):
                cleaned = f"{cleaned} {tw}"
        cleaned = re.sub(r'\s+', ' ', cleaned).strip(" ,;&")
        return cleaned or target_sport

    # 1. Counter-Strike / Esports Check (guard against NFL "Atlanta Falcons" and substring false positives like 'esl' in 'bundesliga' or 'cs' in 'celtics')
    is_nfl_falcons = "atlanta" in combined or sport_raw.lower() in ("nfl", "football", "american football")
    is_cs = not is_nfl_falcons and (
        sport_raw.lower() in ("cs", "cs2", "csgo", "counter-strike", "counter strike", "esports") or
        any(bool(re.search(rf'\b{re.escape(k)}\b', combined)) for k in CS_KEYWORDS)
    )

    def _run_cs(q_str: str) -> str:
        eff_q = _scope_query_for_sport(query_raw, "cs") if (q_str != combined and query_raw) else query_raw
        target_q = " ".join(p for p in (team_raw, player_raw, eff_q) if p).strip() or q_str
        named_event = target_q if any(t in q_str for t in ("thunderpick", "pgl", "blast", "iem", "esl", "major", "bucharest", "chengdu", "atlanta")) else None
        if has_odds_kw and not wants_games_and_odds:
            return _odds_aggregator.get_odds(target_q, sport="cs2")
        elif re.search(r'\b(?:stats|statistics|rating|kdr|kd)\b', q_str):
            return skill_sports_esports(query_type="player_stats", player_url=player_raw or team_raw or target_q)
        elif any(k in q_str for k in ("roster", "player", "lineup", "group", "bracket", "teams", "table", "tables")) or (named_event and "standings" in q_str):
            return skill_sports_esports(query_type="event_info", event_page=named_event or (target_q if target_q not in ("schedule", "") else None))
        elif any(k in q_str for k in ("rank", "top", "leader", "standings")) and not any(k in q_str for k in ("tourn", "schedule", "match", "matches")):
            return skill_sports_esports(query_type="rankings")
        elif any(k in q_str for k in ("live", "stream", "broadcast")):
            return skill_sports_esports(query_type="live_streams")
        elif re.search(r'\b(?:tourn\w*|schedule|next|upcoming|events?|match|matches|time|today|tomorrow|when|playing|plays)\b|\bplay\b(?!\s+(?:a|an|some|me|music|song|track))', q_str):
            return skill_sports_esports(query_type="tournaments", event_page=named_event)
        elif any(k in q_str for k in ("rank", "top", "leader", "standings")):
            return skill_sports_esports(query_type="rankings")
        else:
            is_team_matchup = " vs " in q_str or sum(1 for t in _odds_aggregator.CS_TEAMS if bool(re.search(rf'\b{re.escape(t)}\b', q_str))) >= 2
            return skill_sports_esports(
                query_type="all",
                event_page=named_event,
                matchup=target_q if is_team_matchup else None,
            )

    # 2. Pure Betting Odds Check (when user asks for odds without asking for a full games/schedule list)
    if has_odds_kw and not wants_games_and_odds:
        if is_cs:
            target_q = " ".join(p for p in (team_raw, player_raw, query_raw) if p).strip() or combined
            return _odds_aggregator.get_odds(target_q, sport="cs2")
        return _odds_aggregator.get_odds(combined, sport=sport_raw.lower())

    # 3. Detect all requested sports (supports both single-sport and multi-sport queries like 'soccer and hockey games')
    is_f1 = (
        sport_raw.lower() in ("f1", "formula 1", "formula1", "grand prix", "racing") or
        bool(re.search(r'\b(?:f1|formula\s*1|formula1|grand\s*prix|constructors?|drivers?\s*championship|constructors?\s*championship|verstappen|hamilton|leclerc|norris|piastri|antonelli)\b', combined)) or
        any(k in combined for k in ("f1 race", "f1 standings", "f1 calendar"))
    )

    is_nba = (
        not any(w in combined for w in ("wnba", "ncaab", "college basketball", "march madness")) and (
            sport_raw.lower() in ("nba", "basketball", "hoops") or
            bool(re.search(r'\b(?:nba|basketball|hoops|lakers|celtics|warriors|knicks|sixers|76ers|bucks|cavaliers|cavs|nuggets|mavericks|mavs|suns|clippers|timberwolves|pelicans|rockets|grizzlies|blazers|pacers|pistons|raptors|wizards|hornets|hawks|bulls)\b', combined))
        )
    )

    is_nhl = (
        sport_raw.lower() in ("nhl", "hockey", "ice hockey") or
        bool(re.search(r'\b(?:nhl|hockey|ice\s*hockey|stanley\s*cup|bruins|maple\s*leafs|leafs|canadiens|habs|red\s*wings|lightning|sabres|senators|islanders|devils|penguins|flyers|capitals|hurricanes|blue\s*jackets|oilers|flames|canucks|golden\s*knights|kraken|avalanche|predators|blackhawks|sharks|ducks)\b', combined))
    )

    is_other_us_sport = not is_cs and (
        sport_raw.lower() in ("nfl", "ncaaf", "cfb", "mlb", "baseball", "wnba", "ncaab", "ufc", "mma", "tennis", "atp", "wta", "pga", "golf") or
        any(
            alias in combined
            for reg in _universal_aggregator.SPORT_LEAGUE_REGISTRY.values()
            for alias in reg["aliases"]
        )
    )

    soccer_keywords = (
        "soccer", "epl", "premier", "ucl", "champions",
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
    has_football_as_soccer = (
        "football" in combined
        and not any(w in combined for w in ("nfl", "american football", "college football", "ncaaf"))
    )
    is_explicit_soccer = (
        sport_raw.lower() in ("soccer", "football", "epl", "ucl", "laliga", "seriea", "bundesliga", "mls", "ligue1") or
        has_football_as_soccer or
        any(k in combined for k in soccer_keywords) or
        any(club in combined for club in _soccer_aggregator.CLUB_LEAGUE_HINTS)
    )

    matched_tasks: list[tuple[str, object, str]] = []
    if is_cs:
        matched_tasks.append((SportId.CS, _run_cs, "Present the Counter-Strike tournament/match details clearly."))
    if is_f1:
        matched_tasks.append((SportId.F1, _f1_aggregator.get_info, "Review the Formula 1 schedule/standings and inform the user. Remind them session times are in UTC."))
    if is_nba:
        matched_tasks.append((SportId.NBA, _nba_aggregator.get_info, "Present the NBA games, leaders, injuries, rosters, or standings clearly. Include scores, status, and quarter linescores if available."))
    if is_nhl:
        matched_tasks.append((SportId.NHL, _hockey_aggregator.get_info, "Present the NHL games, leaders, injuries, rosters, or standings clearly. Include scores, status, and period linescores if available."))
    if is_other_us_sport:
        matched_tasks.append((sport_raw or "espn", _universal_aggregator.get_info, "Present the sports scores, schedules, rosters, player stats, injuries, or standings clearly."))
    if is_explicit_soccer:
        matched_tasks.append((SportId.SOCCER, _soccer_aggregator.get_info, "Present the soccer matches, scores, goal scorers, cards, possession/shots stats, lineups, rosters, injuries, or standings clearly."))

    if len(matched_tasks) == 1:
        only_sport, only_fn, guidance = matched_tasks[0]
        if only_sport == SportId.CS and not wants_games_and_odds:
            return only_fn(combined)
        sport_raw = only_sport
        q_arg = _scope_query_for_sport(combined, only_sport) if wants_games_and_odds else combined
        data = only_fn(q_arg)
    elif len(matched_tasks) >= 2:
        sport_raw = ", ".join(t[0] for t in matched_tasks)
        guidance = "Present all requested sports sections clearly under separate headings so every sport the user asked for is covered."

        def _exec_task(item):
            sp_key, fn, _ = item
            return fn(_scope_query_for_sport(combined, sp_key))

        # Run multiple sport aggregators in parallel while preserving ContextVars
        section_outputs = map_in_workers(_exec_task, matched_tasks)
        data = "\n\n" + ("\n\n" + ("=" * 45) + "\n\n").join(s.strip() for s in section_outputs if s and s.strip())
    else:
        # Universal Auto-Discovery Fallback
        disc = espn_search_auto_discover(combined)
        if disc:
            d_sport = disc.get("sport", "")
            d_league = disc.get("league", "")
            if d_sport == SportId.SOCCER:
                sport_raw = SportId.SOCCER
                data = _soccer_aggregator.get_info(combined)
                guidance = "Present the soccer matches, scores, goal scorers, cards, possession/shots stats, lineups, rosters, injuries, or standings clearly."
            elif d_sport == "basketball" and d_league == SportId.NBA:
                sport_raw = SportId.NBA
                data = _nba_aggregator.get_info(combined)
                guidance = "Present the NBA games, leaders, injuries, rosters, or standings clearly."
            elif d_sport == "hockey" and d_league == SportId.NHL:
                sport_raw = SportId.NHL
                data = _hockey_aggregator.get_info(combined)
                guidance = "Present the NHL games, leaders, injuries, rosters, or standings clearly."
            else:
                sport_raw = f"{d_sport}/{d_league}"
                data = _universal_aggregator.get_info(combined)
                guidance = "Present the sports scores, schedules, rosters, player stats, injuries, or standings clearly."
        elif (
            sport_raw.lower() in (SportId.SPORTS, "sport", "general", "all") or
            (sport_raw.lower() == "" and any(k in combined for k in general_match_keywords)) or
            bool(re.search(r'\b(?:sports?)\b', combined))
        ):
            sport_raw = SportId.SOCCER
            data = _soccer_aggregator.get_info(combined)
            guidance = "Present the soccer matches, scores, goal scorers, cards, possession/shots stats, lineups, rosters, injuries, or standings clearly."
        else:
            return (
                f"⚠️ Query '{combined}' is not recognized as a sport, team, or athlete.\n"
                f"`skill_sports_info` ONLY supports sports and esports.\n"
                f"For AI models, technology, coding, general news, or other topics: please use `search_web` instead."
            )

    if wants_games_and_odds:
        if len(matched_tasks) >= 2:
            def _fetch_sport_odds(item) -> str:
                sp_key, _, _ = item
                odds_sp = "cs2" if sp_key == SportId.CS else sp_key
                return _odds_aggregator.get_odds(_scope_query_for_sport(combined, sp_key), sport=odds_sp) or ""

            odds_parts = [sec.strip() for sec in map_in_workers(_fetch_sport_odds, matched_tasks) if sec and sec.strip()]
            odds_section = "\n\n".join(odds_parts)
        else:
            odds_sp = "cs2" if sport_raw == SportId.CS else sport_raw
            odds_section = _odds_aggregator.get_odds(combined, sport=odds_sp)
        if odds_section:
            data = f"{data.strip()}\n\n=== 🎲 BETTING ODDS (DECIMAL FORMAT) ===\n{odds_section.strip()}\n"
            guidance += " Also present the Decimal betting odds (`Best Odds Available`) and win probabilities for each matchup."

    print(f"🏟️ [Executing Sports Info Skill] Sport: {sport_raw}, Query: {combined}")

    now = get_local_now()
    today_str = now.strftime("%A, %B %d, %Y")

    output = f"📅 CURRENT REFERENCE DATE: {today_str}\n\n" + data
    output += build_sports_llm_instructions(today_str, guidance)
    return output

