from src.skills.sports.aggregators import (
    EslLiveAggregator,
    LiquipediaAggregator,
    HltvAggregator,
)

_esl_aggregator = EslLiveAggregator()
_liquipedia_aggregator = LiquipediaAggregator()
_hltv_aggregator = HltvAggregator()

def skill_csgo_esports(query_type: str = 'all', event_page: str = None, player_url: str = None) -> str:
    """
    Use this skill to fetch Counter-Strike (CS) esports information.
    query_type can be:
      - 'tournaments': Gets upcoming and ongoing S-Tier tournaments from Liquipedia.
      - 'event_info': Gets detailed info about a specific event, including CURRENT MATCHES and full TEAM ROSTERS (requires event_page).
      - 'rankings': Gets the current world team rankings from HLTV, including team rosters.
      - 'player_stats': Gets stats for a specific player (requires player_url).
      - 'live_streams': Checks if ESL is currently streaming a live match on YouTube.
      - 'all': Fetches 'tournaments', 'rankings', and 'live_streams'.
    """
    print(f"🎮 [Executing CS Esports Skill] Type: {query_type}")
    output = "=== COUNTER-STRIKE ESPORTS DATA ===\n\n"

    # 1. LIVE STREAMS
    if query_type in ['live_streams', 'all']:
        output += _esl_aggregator.get_live_matches()

    # 2. TOURNAMENTS
    if query_type in ['tournaments', 'all']:
        output += _liquipedia_aggregator.get_s_tier_tournaments()

    # 3. HLTV RANKINGS
    if query_type in ['rankings', 'all']:
        output += _hltv_aggregator.get_rankings()

    # 4. EVENT INFO
    if query_type == 'event_info':
        output += _liquipedia_aggregator.get_event_info(event_page)

    # 5. PLAYER STATS
    if query_type == 'player_stats':
        output += _hltv_aggregator.get_player_stats(player_url)

    output += """=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===
Use this exact data to answer the user's questions about CS esports.
1. When asked about current or live event details (INCLUDING TEAM ROSTERS), FIRST call this tool with `query_type='tournaments'` to get the `event_page` IDs. THEN, call this tool again with `query_type='event_info'` and pass the corresponding `event_page` to get current matches, details, and FULL TEAM ROSTERS. Do not use generic web searches for this!
2. When asked for player stats, check the 'rankings' to get the player's `player_url`, then call this tool with `query_type='player_stats'` and pass the `player_url`.
3. If asked for the roster of a team playing in a live match, DO NOT say you cannot find it. Use `event_info` on the current tournament; it returns the exact rosters!
4. When presenting a LIVE match from YouTube:
   - Actively parse the video 'Title' to figure out who is playing (e.g. 'Team A vs. Team B').
   - Parse the title to determine what stage of the tournament it is (e.g., Playoffs, Group Stage, Grand Final, Semi-Final).
   - Present this dynamically to the user with bold styling for the teams and the stage.
   - Output the raw YouTube link on its own blank line so Discord creates a video embed!
"""
    return output
