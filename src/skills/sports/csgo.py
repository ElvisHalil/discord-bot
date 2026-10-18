from typing import Optional

from src.skills.sports.aggregators import (
    EslLiveAggregator,
    HltvAggregator,
    LiquipediaAggregator,
    OddsAggregator,
)
from src.skills.sports.aggregators.espn_utils import SPORTS_EXECUTOR
from src.skills.sports.enums import CsQueryType
from src.skills.sports.templates import build_cs_llm_instructions

_esl_aggregator = EslLiveAggregator()
_liquipedia_aggregator = LiquipediaAggregator()
_hltv_aggregator = HltvAggregator()
_odds_aggregator = OddsAggregator()


def skill_csgo_esports(
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
        fut_esl = SPORTS_EXECUTOR.submit(_esl_aggregator.get_live_matches)
        if event_page:
            fut_tourn = SPORTS_EXECUTOR.submit(_liquipedia_aggregator.get_s_tier_tournaments, False)
        else:
            fut_tourn = SPORTS_EXECUTOR.submit(_liquipedia_aggregator.get_s_tier_tournaments)
        fut_rank = SPORTS_EXECUTOR.submit(_hltv_aggregator.get_rankings, 7)
        if event_page:
            fut_ev = SPORTS_EXECUTOR.submit(_liquipedia_aggregator.get_event_info, event_page)
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
