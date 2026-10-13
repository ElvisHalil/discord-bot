from typing import Optional
from src.skills.sports.aggregators import (
    EslLiveAggregator,
    LiquipediaAggregator,
    HltvAggregator,
    OddsAggregator,
)

_esl_aggregator = EslLiveAggregator()
_liquipedia_aggregator = LiquipediaAggregator()
_hltv_aggregator = HltvAggregator()
_odds_aggregator = OddsAggregator()

def skill_csgo_esports(query_type: Optional[str] = 'all', event_page: Optional[str] = None, player_url: Optional[str] = None, matchup: Optional[str] = None) -> str:
    """
    Use this skill to fetch Counter-Strike (CS) esports information.
    query_type can be:
      - 'tournaments': Gets upcoming and ongoing S-Tier tournaments from Liquipedia.
      - 'event_info': Gets detailed info about a specific event, including CURRENT MATCHES and full TEAM ROSTERS (requires event_page).
      - 'rankings': Gets the current world team rankings from HLTV, including team rosters.
      - 'player_stats': Gets stats for a specific player (requires player_url).
      - 'live_streams': Checks if ESL is currently streaming a live match on YouTube.
      - 'odds': Gets live betting odds, moneylines, and win probabilities for a matchup (pass matchup or event_page).
      - 'all': Fetches 'tournaments', 'rankings', and 'live_streams'.

    Args:
        query_type: One of 'tournaments', 'event_info', 'rankings', 'player_stats', 'live_streams', 'odds', 'all' (default).
        event_page: Liquipedia event page, required for query_type='event_info'.
        player_url: Player profile URL, required for query_type='player_stats'.
        matchup: Team matchup for betting odds (e.g. 'Falcons vs Ninjas in Pyjamas'), used for query_type='odds'.
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
        limit = 7 if query_type == 'all' else 15
        output += _hltv_aggregator.get_rankings(limit=limit)

    # 4. EVENT INFO
    if query_type == 'event_info':
        output += _liquipedia_aggregator.get_event_info(event_page)

    # 5. PLAYER STATS
    if query_type == 'player_stats':
        output += _hltv_aggregator.get_player_stats(player_url)

    # 6. BETTING ODDS
    if query_type in ['odds', 'betting']:
        target = matchup or event_page or "Falcons vs Ninjas in Pyjamas"
        output += _odds_aggregator.get_odds(target, sport='cs2')

    output += """=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===
Use this exact live data to answer the user's questions about CS esports.
1. When answering general questions (e.g. "what's happening in cs", "cs news", "esports update"):
   - Synthesize a lively, cohesive overview instead of dumping raw rigid sections or boilerplate templates.
   - Highlight what is happening NEXT: the immediate upcoming tournament starting soonest (from UPCOMING S-TIER TOURNAMENTS) with dates, prize pool, location, and its clickable Markdown link: `[Tournament Name](<https://liquipedia.net/...>)`.
   - Highlight recent action: mention the most recent tournament champion and runner-up (from RECENT TOURNAMENT CHAMPIONS).
   - Mention top world ranking leaders concisely (e.g. #1 Spirit, #2 Vitality) without dumping long lists of all players unless the user asked for rosters.
   - If there is an active ESL YouTube live stream, highlight the matchup and stream link. If ESL is NOT broadcasting live, simply omit the live section or mention in passing that no games are live right now—DO NOT create a prominent empty section.
   - NEVER output pseudo-links like `[Event Page](<event_page: ...>)` or `<event_page: ...>`. Only use real HTTPS links with angle brackets: `[Tournament](<https://liquipedia.net/...>)`.
   - Do NOT end your message with generic canned questions (e.g. "Need details about a specific team, tournament...?"). Converse naturally like an esports enthusiast.
2. When asked "what teams are participating":
   - Present participating teams cleanly (e.g. grouped by Group A / Group B or with bold bullet points).
   - Do NOT dump long walls of bracketed player names like `Team Falcons (p1, p2, p3, p4, p5, coach)`.
   - If the user explicitly asks for rosters, list active players and coach cleanly on an indented sub-bullet:
     - **Team Falcons**: NiKo, TeSeS, m0NESY, kyousuke, karrigan *(Coach: zonic)*
3. When asked about match odds or betting lines:
   - Use `query_type='odds'` or `skill_betting_odds` with the matchup (e.g. 'Falcons vs Ninjas in Pyjamas').
   - State the favorite, underdog, decimal/American odds, and win probabilities directly.
   - NEVER claim that you cannot check odds or tell the user to visit websites manually!
4. When asked for player stats:
   - Check 'rankings' to get the player's `player_url`, then call this tool with `query_type='player_stats'` and pass the `player_url`.
5. When presenting a LIVE match from YouTube:
   - Parse the title to determine who is playing and the stage (e.g. Grand Final, Semifinal).
   - Output the raw YouTube link on its own blank line so Discord generates an embed.
"""
    return output
